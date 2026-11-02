"""
Applique chatagent/db/schema.sql sur Neon (schema Yayra ou ?schema= dans DATABASE_URL).

Usage (depuis chatagentapi/) :
  py scripts/apply_neon_schema.py
  py scripts/apply_neon_schema.py --schema public
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

_API_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _API_DIR.parent
_SCHEMA = _REPO_ROOT / "chatagent" / "db" / "schema.sql"


def _load_dotenv() -> None:
    path = _API_DIR / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and (key not in os.environ or key == "DATABASE_URL"):
            os.environ[key] = val


def _psycopg_connect_url(url: str) -> str:
    from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

    parsed = urlparse(url)
    if not parsed.query:
        return url
    params = parse_qs(parsed.query, keep_blank_values=True)
    params.pop("schema", None)
    new_query = urlencode({k: v[0] for k, v in params.items()}, doseq=False)
    return urlunparse(parsed._replace(query=new_query))


def _schema_from_url(url: str) -> str | None:
    from urllib.parse import parse_qs, urlparse

    return (parse_qs(urlparse(url).query).get("schema") or [None])[0]


def _split_sql(script: str) -> list[str]:
    """Decoupe SQL en statements (supporte blocs $$)."""
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(script)
    dollar_tag: str | None = None

    while i < n:
        if dollar_tag is None and script.startswith("$$", i):
            m = re.match(r"\$([A-Za-z0-9_]*)\$", script[i:])
            if m:
                dollar_tag = m.group(1)
                buf.append(m.group(0))
                i += len(m.group(0))
                continue
        if dollar_tag is not None:
            end = f"${dollar_tag}$"
            if script.startswith(end, i):
                buf.append(end)
                i += len(end)
                dollar_tag = None
                continue
            buf.append(script[i])
            i += 1
            continue

        ch = script[i]
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _executable(stmt: str) -> bool:
    without_comments = re.sub(r"--[^\n]*", "", stmt)
    return bool(without_comments.strip())


def main() -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--schema",
        default="",
        help='Schema cible (defaut: param schema= dans DATABASE_URL ou "Yayra")',
    )
    args = parser.parse_args()

    raw_url = os.environ.get("DATABASE_URL", "").strip()
    if not raw_url:
        print("DATABASE_URL manquant dans chatagentapi/.env")
        return 1
    if not _SCHEMA.is_file():
        print(f"Fichier introuvable : {_SCHEMA}")
        return 1

    target_schema = (args.schema or _schema_from_url(raw_url) or "Yayra").strip()

    try:
        import psycopg2
    except ImportError:
        print("Installez psycopg2-binary")
        return 1

    conn = psycopg2.connect(_psycopg_connect_url(raw_url))
    conn.autocommit = True

    bootstrap = f"""
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS "{target_schema}";
SET search_path TO "{target_schema}", public;
"""
    body = _SCHEMA.read_text(encoding="utf-8")
    # schema.sql re-declare extensions; safe with IF NOT EXISTS
    full = bootstrap + "\n" + body

    try:
        with conn.cursor() as cur:
            for stmt in _split_sql(full):
                if not _executable(stmt):
                    continue
                try:
                    cur.execute(stmt)
                except Exception as exc:
                    msg = str(exc).lower()
                    if "already exists" in msg or "duplicate" in msg:
                        continue
                    print(f"Erreur SQL: {exc}\n---\n{stmt[:400]}...\n---")
                    return 1
        print(f"Schema Yayra applique dans le schema \"{target_schema}\".")
        print("Prochaine etape : py scripts/migrate_supabase_to_neon.py --via-api --dry-run")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
