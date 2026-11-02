"""
Copie les données Supabase (Postgres direct) vers Neon (schéma Yayra).

Prérequis:
  1. Neon : psql $DATABASE_URL -f chatagent/db/schema.sql
  2. Supabase → Settings → Database → URI (postgres, port 5432)
  3. Variables d'environnement :
     SUPABASE_DATABASE_URL=postgresql://postgres.[ref]:[pwd]@...supabase.com:5432/postgres
     DATABASE_URL=postgresql://...@neon.tech/...

Usage:
  py scripts/migrate_supabase_to_neon.py --dry-run
  py scripts/migrate_supabase_to_neon.py
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

_API_DIR = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    for name in (".env",):
        path = _API_DIR / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if not key:
                continue
            if key not in os.environ or key in (
                "DATABASE_URL",
                "SUPABASE_URL",
                "SUPABASE_SERVICE_KEY",
                "SUPABASE_DB_PASSWORD",
            ):
                os.environ[key] = val


def _resolve_supabase_database_url() -> str:
    direct = os.environ.get("SUPABASE_DATABASE_URL", "").strip()
    placeholders = ("YOUR_DB_PASSWORD", "[YOUR-PASSWORD]", "[YOUR-PASSWORD]")
    if direct and not any(p in direct for p in placeholders):
        return direct

    password = os.environ.get("SUPABASE_DB_PASSWORD", "").strip()
    project = os.environ.get("SUPABASE_PROJECT_REF", "").strip()
    if not project:
        supabase_url = os.environ.get("SUPABASE_URL", "").strip()
        m = re.search(r"https://([a-z0-9]+)\.supabase\.co", supabase_url, re.I)
        if m:
            project = m.group(1)

    if password and project:
        user = os.environ.get("SUPABASE_DB_USER", "postgres").strip() or "postgres"
        host = os.environ.get("SUPABASE_DB_HOST", f"db.{project}.supabase.co").strip()
        port = os.environ.get("SUPABASE_DB_PORT", "5432").strip() or "5432"
        db = os.environ.get("SUPABASE_DB_NAME", "postgres").strip() or "postgres"
        return (
            f"postgresql://{quote_plus(user)}:{quote_plus(password)}"
            f"@{host}:{port}/{db}"
        )

    raise SystemExit(
        "Connexion Postgres Supabase manquante.\n"
        "Option A — dans chatagentapi/.env :\n"
        "  SUPABASE_DB_PASSWORD=<mot de passe Database Supabase>\n"
        "  (SUPABASE_URL est déjà utilisé pour déduire db.[ref].supabase.co)\n"
        "Option B — URI complète :\n"
        "  SUPABASE_DATABASE_URL=postgresql://postgres:...@db.[ref].supabase.co:5432/postgres\n"
        "Mot de passe : Supabase → Project Settings → Database "
        "(Reset database password si oublié). Ce n'est pas SUPABASE_SERVICE_KEY."
    )


def _validate_postgres_url(url: str, var_name: str) -> str:
    u = url.strip()
    if not u:
        raise SystemExit(f"{var_name} manquant — ajoutez-le dans chatagentapi/.env")
    if u.startswith("https://") or u.startswith("http://"):
        raise SystemExit(
            f"{var_name} ne doit pas être l'URL du projet Supabase (https://…).\n"
            "Utilisez l'URI Postgres :\n"
            "  Supabase → Project Settings → Database → Connection string → URI\n"
            "  Exemple : postgresql://postgres.[ref]:[MOT_DE_PASSE]@db.[ref].supabase.co:5432/postgres\n"
            "(ou Session pooler port 5432 — pas l'URL https://xxx.supabase.co)"
        )
    if not (u.startswith("postgresql://") or u.startswith("postgres://")):
        raise SystemExit(
            f"{var_name} invalide : attendu postgresql://… (reçu : {u[:40]}…)"
        )
    return u


def _connect(url: str, label: str):
    try:
        import psycopg2
    except ImportError as exc:
        raise SystemExit("Installez psycopg2-binary") from exc
    try:
        return psycopg2.connect(_psycopg_connect_url(url))
    except Exception as exc:
        raise SystemExit(f"Connexion {label} impossible: {exc}") from exc


def _table_exists(cur, schema: str, table: str) -> bool:
    cur.execute(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = %s AND table_name = %s
        """,
        (schema, table),
    )
    return cur.fetchone() is not None


def _table_exists_on_path(cur, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (table,))
    row = cur.fetchone()
    if row and row[0]:
        return True
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",))
    row = cur.fetchone()
    return bool(row and row[0])


def _columns(cur, schema: str, table: str) -> list[str]:
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """,
        (schema, table),
    )
    return [row[0] for row in cur.fetchall()]


def _columns_on_path(cur, table: str) -> list[str]:
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = %s
          AND table_schema = ANY(current_schemas(true))
        ORDER BY ordinal_position
        """,
        (table,),
    )
    cols = [row[0] for row in cur.fetchall()]
    if cols:
        return cols
    return _columns(cur, "public", table)


def _copy_public_table(
    src_cur,
    dst_cur,
    table: str,
    *,
    dry_run: bool,
    extra_casts: dict[str, str] | None = None,
) -> int:
    if not _table_exists(src_cur, "public", table):
        print(f"  [skip] public.{table} absent côté Supabase")
        return 0
    if not _table_exists_on_path(dst_cur, table):
        print(f"  [skip] {table} absent cote Neon — appliquez schema.sql")
        return 0

    src_cols = set(_columns(src_cur, "public", table))
    cols = [c for c in _columns_on_path(dst_cur, table) if c in src_cols]
    if not cols:
        print(f"  [skip] {table} : aucune colonne commune")
        return 0

    src_cur.execute(f"SELECT {', '.join(cols)} FROM public.{table}")
    rows = src_cur.fetchall()
    if not rows:
        print(f"  {table}: 0 ligne")
        return 0

    if dry_run:
        print(f"  {table}: {len(rows)} ligne(s) (dry-run)")
        return len(rows)

    inserted = 0
    for row in rows:
        values: list[Any] = list(row)
        ph = []
        for i, col in enumerate(cols):
            cast = (extra_casts or {}).get(col)
            val = values[i]
            if cast and val is not None:
                ph.append(f"%s{cast}")
            else:
                ph.append("%s")
        sql = (
            f"INSERT INTO {table} ({', '.join(cols)}) "
            f"VALUES ({', '.join(ph)}) "
            f"ON CONFLICT DO NOTHING"
        )
        dst_cur.execute(sql, values)
        inserted += dst_cur.rowcount
    print(f"  {table}: {inserted}/{len(rows)} inseree(s)")
    return inserted


TABLES_ORDER = [
    "organizations",
    "sites",
    "organization_members",
    "site_documents",
    "crawled_pages",
    "knowledge_chunks",
    "visitors",
    "traffic_links",
    "conversations",
    "messages",
    "leads",
    "widget_click_events",
    "notifications",
    "agent_device_tokens",
]


def _neon_schema_from_url(url: str) -> str | None:
    from urllib.parse import parse_qs, urlparse

    return (parse_qs(urlparse(url).query).get("schema") or [None])[0]


def _psycopg_connect_url(url: str) -> str:
    """Retire les query params non supportés par libpq (ex. schema=Yayra côté Neon)."""
    from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

    parsed = urlparse(url)
    if not parsed.query:
        return url
    params = parse_qs(parsed.query, keep_blank_values=True)
    params.pop("schema", None)
    new_query = urlencode({k: v[0] for k, v in params.items()}, doseq=False)
    return urlunparse(parsed._replace(query=new_query))


def _configure_neon_search_path(conn, dst_url: str) -> None:
    schema = _neon_schema_from_url(dst_url)
    if not schema:
        return
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema}", public')
    conn.commit()


def _supabase_rest_headers(service_key: str) -> dict[str, str]:
    return {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
    }


def _http_get_json(url: str, headers: dict[str, str]) -> tuple[int, Any]:
    import json
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    req = Request(url, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=120) as res:
            body = res.read().decode("utf-8")
            code = res.getcode() or 200
            return code, json.loads(body) if body else None
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body) if body else None
        except json.JSONDecodeError:
            payload = body
        return exc.code, payload


def _fetch_rest_table(base_url: str, service_key: str, table: str) -> list[dict]:
    headers = _supabase_rest_headers(service_key)
    rows: list[dict] = []
    offset = 0
    page_size = 1000
    while True:
        url = f"{base_url.rstrip('/')}/rest/v1/{table}?select=*"
        range_headers = {
            **headers,
            "Range-Unit": "items",
            "Range": f"{offset}-{offset + page_size - 1}",
        }
        code, batch = _http_get_json(url, range_headers)
        if code == 404:
            return []
        if code >= 400:
            raise SystemExit(f"REST {table} HTTP {code}: {batch}")
        if not isinstance(batch, list):
            break
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return rows


def _fetch_auth_users_api(base_url: str, service_key: str) -> list[dict]:
    headers = _supabase_rest_headers(service_key)
    users: list[dict] = []
    page = 1
    while True:
        url = f"{base_url.rstrip('/')}/auth/v1/admin/users?page={page}&per_page=1000"
        code, payload = _http_get_json(url, headers)
        if code >= 400:
            raise SystemExit(f"Auth admin HTTP {code}: {payload}")
        batch = payload.get("users") if isinstance(payload, dict) else None
        if not batch:
            break
        users.extend(batch)
        if len(batch) < 1000:
            break
        page += 1
    return users


def _jsonb_columns(cur, table: str) -> set[str]:
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = %s
          AND table_schema = ANY(current_schemas(true))
          AND udt_name = 'jsonb'
        """,
        (table,),
    )
    cols = {row[0] for row in cur.fetchall()}
    if cols:
        return cols
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s AND udt_name = 'jsonb'
        """,
        (table,),
    )
    return {row[0] for row in cur.fetchall()}


def _adapt_api_value(col: str, val: Any, *, vector_cols: set[str], jsonb_cols: set[str]) -> Any:
    if val is None:
        return None
    if col in vector_cols:
        if isinstance(val, list):
            return "[" + ",".join(str(float(x)) for x in val) + "]"
        return val
    if col in jsonb_cols and isinstance(val, (dict, list)):
        from psycopg2.extras import Json

        return Json(val)
    return val


def _insert_rows_api(
    dst_cur,
    table: str,
    rows: list[dict],
    *,
    dry_run: bool,
    vector_cols: set[str] | None = None,
) -> int:
    if not rows:
        print(f"  {table}: 0 ligne")
        return 0
    if not _table_exists_on_path(dst_cur, table):
        print(f"  [skip] {table} absent cote Neon — appliquez schema.sql (search_path actuel)")
        return 0

    dst_cols = _columns_on_path(dst_cur, table)
    dst_set = set(dst_cols)
    inserted = 0
    if dry_run:
        print(f"  {table}: {len(rows)} ligne(s) (dry-run, API)")
        return len(rows)

    vector_cols = vector_cols or set()
    jsonb_cols = _jsonb_columns(dst_cur, table)
    for raw in rows:
        cols = [c for c in dst_cols if c in raw]
        if not cols:
            continue
        values = [
            _adapt_api_value(c, raw.get(c), vector_cols=vector_cols, jsonb_cols=jsonb_cols)
            for c in cols
        ]
        ph = []
        for i, col in enumerate(cols):
            if col in vector_cols and values[i] is not None:
                ph.append("%s::vector")
            else:
                ph.append("%s")
        sql = (
            f"INSERT INTO {table} ({', '.join(cols)}) "
            f"VALUES ({', '.join(ph)}) ON CONFLICT DO NOTHING"
        )
        dst_cur.execute(sql, values)
        inserted += dst_cur.rowcount
    print(f"  {table}: {inserted}/{len(rows)} inseree(s) (API)")
    return inserted


def migrate_auth_users_api(dst_cur, users: list[dict], *, dry_run: bool) -> int:
    if not _table_exists_on_path(dst_cur, "app_users"):
        dst_cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = ANY(current_schemas(false)) ORDER BY 1 LIMIT 15"
        )
        found = [r[0] for r in dst_cur.fetchall()]
        dst_cur.execute("SHOW search_path")
        sp = dst_cur.fetchone()[0]
        raise SystemExit(
            "app_users manquante sur Neon.\n"
            f"  search_path actuel : {sp}\n"
            f"  tables vues : {found or '(aucune)'}\n"
            "  -> Executez chatagent/db/schema.sql sur ce schema (Neon SQL Editor),\n"
            "     ou migrez vers public et enlevez schema=Yayra de DATABASE_URL."
        )
    if dry_run:
        print(f"  app_users (API auth): {len(users)} ligne(s) (dry-run, sans mots de passe)")
        return len(users)

    n = 0
    for u in users:
        email = (u.get("email") or "").strip().lower()
        uid = u.get("id")
        if not email or not uid:
            continue
        confirmed = u.get("email_confirmed_at") or u.get("confirmed_at")
        created = u.get("created_at")
        updated = u.get("updated_at") or created
        dst_cur.execute(
            """
            INSERT INTO app_users (id, email, password_hash, email_verified_at, created_at, updated_at)
            VALUES (%s, %s, NULL, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
              email = EXCLUDED.email,
              email_verified_at = COALESCE(app_users.email_verified_at, EXCLUDED.email_verified_at)
            """,
            (uid, email, confirmed, created, updated),
        )
        n += 1
    print(
        f"  app_users: {n} compte(s) (API — pas de hash mot de passe ; reset via « mot de passe oublié »)"
    )
    return n


def run_via_api(*, dry_run: bool) -> int:
    base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not base.startswith("https://"):
        raise SystemExit("SUPABASE_URL manquant (https://xxx.supabase.co)")
    if not key:
        raise SystemExit("SUPABASE_SERVICE_KEY manquant dans .env")

    dst_url = _validate_postgres_url(os.environ.get("DATABASE_URL", ""), "DATABASE_URL")
    dst = _connect(dst_url, "Neon")
    _configure_neon_search_path(dst, dst_url)

    try:
        print("=== Comptes (Auth Admin API -> app_users) ===")
        users = _fetch_auth_users_api(base, key)
        with dst.cursor() as dst_cur:
            migrate_auth_users_api(dst_cur, users, dry_run=dry_run)

            print("=== Tables public.* (REST) ===")
            for table in TABLES_ORDER:
                rows = _fetch_rest_table(base, key, table)
                vc = {"embedding"} if table == "knowledge_chunks" else set()
                _insert_rows_api(dst_cur, table, rows, dry_run=dry_run, vector_cols=vc)

            if not dry_run:
                dst.commit()
                print("Migration API terminée (commit OK).")
            else:
                print("Dry-run API terminé — aucune écriture.")
    finally:
        dst.close()

    print(
        "\nMode --via-api : les mots de passe Supabase ne sont pas exportables.\n"
        "  • Demandez aux utilisateurs « Mot de passe oublié »\n"
        "  • Ou refaites la migration en Postgres direct (SUPABASE_DB_PASSWORD) pour garder les bcrypt\n"
    )
    return 0


def migrate_auth_users(src_cur, dst_cur, *, dry_run: bool) -> int:
    if not _table_exists(src_cur, "auth", "users"):
        print("  [skip] auth.users introuvable")
        return 0
    if not _table_exists_on_path(dst_cur, "app_users"):
        raise SystemExit("app_users manquante sur Neon")

    src_cur.execute(
        """
        SELECT id, email, encrypted_password, email_confirmed_at, created_at, updated_at
        FROM auth.users
        ORDER BY created_at
        """
    )
    rows = src_cur.fetchall()
    if dry_run:
        print(f"  app_users (depuis auth.users): {len(rows)} ligne(s) (dry-run)")
        return len(rows)

    n = 0
    for uid, email, enc, confirmed_at, created_at, updated_at in rows:
        if not email:
            continue
        pwd = f"bcrypt:{enc}" if enc else None
        dst_cur.execute(
            """
            INSERT INTO app_users (id, email, password_hash, email_verified_at, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
              email = EXCLUDED.email,
              password_hash = COALESCE(app_users.password_hash, EXCLUDED.password_hash),
              email_verified_at = COALESCE(app_users.email_verified_at, EXCLUDED.email_verified_at)
            """,
            (uid, email.lower(), pwd, confirmed_at, created_at, updated_at),
        )
        n += 1
    print(f"  app_users: {n} compte(s) migré(s) (mots de passe préfixe bcrypt:)")
    return n


def main() -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="Migration Supabase Postgres → Neon Yayra")
    parser.add_argument("--dry-run", action="store_true", help="Compte les lignes sans écrire")
    parser.add_argument(
        "--supabase-url",
        dest="supabase_url",
        default="",
        help="URI Postgres Supabase (prioritaire sur SUPABASE_DATABASE_URL)",
    )
    parser.add_argument(
        "--via-api",
        action="store_true",
        help="Sans mot de passe DB : SUPABASE_URL + SUPABASE_SERVICE_KEY (pas de hash mots de passe)",
    )
    args = parser.parse_args()

    if args.via_api:
        return run_via_api(dry_run=args.dry_run)

    src_raw = (args.supabase_url or _resolve_supabase_database_url()).strip()
    src_url = _validate_postgres_url(src_raw, "SUPABASE_DATABASE_URL")
    dst_url = _validate_postgres_url(os.environ.get("DATABASE_URL", ""), "DATABASE_URL")

    src = _connect(src_url, "Supabase")
    dst = _connect(dst_url, "Neon")
    _configure_neon_search_path(dst, dst_url)

    try:
        with src.cursor() as src_cur, dst.cursor() as dst_cur:
            print("=== Comptes (auth.users -> app_users) ===")
            migrate_auth_users(src_cur, dst_cur, dry_run=args.dry_run)

            print("=== Tables public.* ===")
            vector_cast = {"embedding": "::vector"}
            for table in TABLES_ORDER:
                casts = vector_cast if table == "knowledge_chunks" else None
                _copy_public_table(src_cur, dst_cur, table, dry_run=args.dry_run, extra_casts=casts)

            if not args.dry_run:
                dst.commit()
                print("Migration terminée (commit OK).")
            else:
                print("Dry-run terminé — aucune écriture.")
    finally:
        src.close()
        dst.close()

    print(
        "\nÉtapes suivantes:\n"
        "  • Vérifier AUTH_SECRET identique web + API\n"
        "  • Tester connexion web/mobile (hash bcrypt migre -> scrypt au prochain login)\n"
        "  • Re-téléverser logos/PDF si stockés dans Supabase Storage (non copiés ici)\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
