"""PostgreSQL (Neon) — remplace le client Supabase PostgREST."""

from __future__ import annotations

import json
import logging
import re
from contextlib import contextmanager
from typing import Any

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)


def _values_are_uuids(values: list[Any]) -> bool:
    return bool(values) and all(isinstance(v, str) and _UUID_RE.match(v) for v in values)

import psycopg2
import psycopg2.extras

from app.config import settings

logger = logging.getLogger(__name__)


def _parse_database_url() -> tuple[str, str | None]:
    raw = settings.database_url.strip()
    if not raw:
        raise RuntimeError("DATABASE_URL non configurée")
    from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

    parsed = urlparse(raw)
    schema = (parse_qs(parsed.query).get("schema") or [None])[0]
    if schema:
        params = parse_qs(parsed.query, keep_blank_values=True)
        params.pop("schema", None)
        query = urlencode({k: v[0] for k, v in params.items()}, doseq=False)
        parsed = parsed._replace(query=query)
    url = urlunparse(parsed)
    return url, schema


@contextmanager
def get_connection():
    url, schema = _parse_database_url()
    conn = psycopg2.connect(url)
    try:
        if schema and schema.replace("_", "").isalnum():
            with conn.cursor() as cur:
                cur.execute(f'SET search_path TO "{schema}", public')
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _adapt_value(key: str, value: Any) -> Any:
    if isinstance(value, (dict, list)) and key != "embedding":
        return psycopg2.extras.Json(value)
    if key == "embedding" and isinstance(value, list):
        return "[" + ",".join(str(float(x)) for x in value) + "]"
    return value


class _ExecuteResult:
    def __init__(self, data: Any = None, count: int | None = None):
        self.data = data
        self.count = count


class TableQuery:
    def __init__(self, table: str):
        self.table = table
        self._mode = "select"
        self._select = "*"
        self._filters: list[str] = []
        self._params: list[Any] = []
        self._order = ""
        self._limit: int | None = None
        self._insert: dict[str, Any] | list[dict[str, Any]] | None = None
        self._update: dict[str, Any] | None = None
        self._upsert: dict[str, Any] | None = None
        self._upsert_conflict: str | None = None
        self._single: str | None = None
        self._count_head = False

    def select(self, columns: str, count: str | None = None, head: bool = False):
        self._select = columns
        if count == "exact" and head:
            self._count_head = True
        return self

    def insert(self, row: dict[str, Any] | list[dict[str, Any]]):
        self._mode = "insert"
        self._insert = row
        return self

    def update(self, row: dict[str, Any]):
        self._mode = "update"
        self._update = row
        return self

    def upsert(self, row: dict[str, Any], on_conflict: str | None = None):
        self._mode = "upsert"
        self._upsert = row
        self._upsert_conflict = on_conflict
        return self

    def delete(self):
        self._mode = "delete"
        return self

    def eq(self, column: str, value: Any):
        self._filters.append(f"{column} = %s")
        self._params.append(value)
        return self

    def gt(self, column: str, value: Any):
        self._filters.append(f"{column} > %s")
        self._params.append(value)
        return self

    def gte(self, column: str, value: Any):
        self._filters.append(f"{column} >= %s")
        self._params.append(value)
        return self

    def lt(self, column: str, value: Any):
        self._filters.append(f"{column} < %s")
        self._params.append(value)
        return self

    def lte(self, column: str, value: Any):
        self._filters.append(f"{column} <= %s")
        self._params.append(value)
        return self

    def in_(self, column: str, values: list[Any]):
        if not values:
            self._filters.append("FALSE")
            return self
        if _values_are_uuids(values):
            self._filters.append(f"{column} = ANY(%s::uuid[])")
        else:
            self._filters.append(f"{column} = ANY(%s)")
        self._params.append(values)
        return self

    def is_(self, column: str, value: Any):
        if value is None or value == "null":
            self._filters.append(f"{column} IS NULL")
        return self

    def ilike(self, column: str, pattern: str):
        self._filters.append(f"{column} ILIKE %s")
        self._params.append(pattern)
        return self

    def order(self, column: str, desc: bool = False):
        direction = "DESC" if desc else "ASC"
        self._order = f"ORDER BY {column} {direction}"
        return self

    def limit(self, n: int):
        self._limit = n
        return self

    def single(self):
        self._single = "single"
        return self

    def maybe_single(self):
        self._single = "maybe"
        return self

    def execute(self) -> _ExecuteResult:
        where = f"WHERE {' AND '.join(self._filters)}" if self._filters else ""
        limit = f"LIMIT {self._limit}" if self._limit is not None else ""

        from app.services.db_errors import APIError

        try:
            with get_connection() as conn:
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

                if self._mode == "select":
                    if self._count_head:
                        cur.execute(
                            f"SELECT COUNT(*) AS count FROM {self.table} {where}", self._params
                        )
                        row = cur.fetchone()
                        return _ExecuteResult(count=row["count"] if row else 0)

                    cur.execute(
                        f"SELECT {self._select} FROM {self.table} {where} {self._order} {limit}".strip(),
                        self._params,
                    )
                    rows = [dict(r) for r in cur.fetchall()]
                    if self._single == "single":
                        if not rows:
                            return _ExecuteResult(data=None)
                        return _ExecuteResult(data=rows[0])
                    if self._single == "maybe":
                        return _ExecuteResult(data=rows[0] if rows else None)
                    return _ExecuteResult(data=rows)

                if self._mode == "insert" and self._insert is not None:
                    rows = self._insert if isinstance(self._insert, list) else [self._insert]
                    keys = list(rows[0].keys())
                    values_sql = []
                    flat: list[Any] = []
                    for row in rows:
                        values_sql.append("(" + ", ".join(["%s"] * len(keys)) + ")")
                        for k in keys:
                            flat.append(_adapt_value(k, row[k]))
                    cur.execute(
                        f"INSERT INTO {self.table} ({', '.join(keys)}) VALUES {', '.join(values_sql)} RETURNING *",
                        flat,
                    )
                    rows = [dict(r) for r in cur.fetchall()]
                    if self._single == "single":
                        return _ExecuteResult(data=rows[0] if rows else None)
                    if self._single == "maybe":
                        return _ExecuteResult(data=rows[0] if rows else None)
                    return _ExecuteResult(data=rows)

                if self._mode == "update" and self._update is not None:
                    keys = list(self._update.keys())
                    set_parts = []
                    vals: list[Any] = []
                    for k in keys:
                        v = _adapt_value(k, self._update[k])
                        if k == "embedding" and isinstance(v, str):
                            set_parts.append(f"{k} = %s::vector")
                        else:
                            set_parts.append(f"{k} = %s")
                        vals.append(v)
                    set_clause = ", ".join(set_parts)
                    returning = f"RETURNING {self._select}" if self._single else ""
                    cur.execute(
                        f"UPDATE {self.table} SET {set_clause} {where} {returning}".strip(),
                        vals + self._params,
                    )
                    if self._single:
                        out = cur.fetchone()
                        return _ExecuteResult(data=dict(out) if out else None)
                    return _ExecuteResult(data=[])

                if self._mode == "delete":
                    cur.execute(f"DELETE FROM {self.table} {where}", self._params)
                    return _ExecuteResult(data=None)

                if self._mode == "upsert" and self._upsert is not None:
                    keys = list(self._upsert.keys())
                    vals = [_adapt_value(k, self._upsert[k]) for k in keys]
                    conflict = self._upsert_conflict or keys[0]
                    updates = ", ".join(f"{k} = EXCLUDED.{k}" for k in keys)
                    cur.execute(
                        f"INSERT INTO {self.table} ({', '.join(keys)}) VALUES ({', '.join(['%s'] * len(keys))}) "
                        f"ON CONFLICT ({conflict}) DO UPDATE SET {updates} RETURNING *",
                        vals,
                    )
                    out = cur.fetchone()
                    return _ExecuteResult(data=[dict(out)] if out else [])

                return _ExecuteResult(data=None)
        except APIError:
            raise
        except Exception as exc:
            raise APIError(str(exc)) from exc


class RpcQuery:
    def __init__(self, fn: str, params: dict[str, Any]):
        self.fn = fn
        self.params = params

    def execute(self) -> _ExecuteResult:
        if self.fn == "match_knowledge":
            with get_connection() as conn:
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                embedding = self.params["query_embedding"]
                emb_str = "[" + ",".join(str(float(x)) for x in embedding) + "]"
                cur.execute(
                    """
                    SELECT id, content, title, source_url, similarity
                    FROM match_knowledge(%s::vector, %s::uuid, %s, %s)
                    """,
                    (
                        emb_str,
                        self.params["match_site_id"],
                        self.params["match_count"],
                        self.params["match_threshold"],
                    ),
                )
                return _ExecuteResult(data=[dict(r) for r in cur.fetchall()])
        raise RuntimeError(f"RPC inconnu: {self.fn}")


class AuthUserResponse:
    def __init__(self, user: Any):
        self.user = user


class AuthModule:
    def get_user(self, token: str) -> AuthUserResponse:
        from jose import JWTError, jwt

        secret = settings.auth_jwt_secret
        if not secret:
            raise RuntimeError("AUTH_SECRET requis")
        try:
            payload = jwt.decode(token, secret, algorithms=["HS256"])
            user_id = payload.get("sub")
            email = payload.get("email")
        except JWTError as exc:
            raise ValueError("Invalid token") from exc
        if not user_id:
            raise ValueError("Invalid token")
        user = type("User", (), {"id": user_id, "email": email})()
        return AuthUserResponse(user)


class PostgresClient:
    def table(self, name: str) -> TableQuery:
        return TableQuery(name)

    def rpc(self, fn: str, params: dict[str, Any]) -> RpcQuery:
        return RpcQuery(fn, params)

    @property
    def auth(self) -> AuthModule:
        return AuthModule()


_client: PostgresClient | None = None


def get_supabase() -> PostgresClient:
    global _client
    if _client is None:
        _client = PostgresClient()
    return _client
