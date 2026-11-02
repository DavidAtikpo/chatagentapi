"""
Importe les mots de passe Supabase (auth.users) vers app_users sur Neon.

Requis après migration --via-api (comptes sans password_hash).

  SUPABASE_DB_PASSWORD=...   ou SUPABASE_DATABASE_URL=postgresql://...
  DATABASE_URL=...           (Neon, schema=Yayra OK)

Usage:
  py scripts/migrate_supabase_passwords.py --dry-run
  py scripts/migrate_supabase_passwords.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_API_DIR = Path(__file__).resolve().parents[1]

# Réutilise helpers du script de migration
sys.path.insert(0, str(_API_DIR / "scripts"))
from migrate_supabase_to_neon import (  # noqa: E402
    _configure_neon_search_path,
    _connect,
    _load_dotenv,
    _psycopg_connect_url,
    _resolve_supabase_database_url,
    _validate_postgres_url,
)


def main() -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="Import bcrypt Supabase -> app_users Neon")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--supabase-db-password",
        dest="supabase_db_password",
        default="",
        help="Mot de passe Database Supabase (sinon SUPABASE_DB_PASSWORD dans .env)",
    )
    args = parser.parse_args()

    if args.supabase_db_password:
        os.environ["SUPABASE_DB_PASSWORD"] = args.supabase_db_password.strip()

    src_url = _validate_postgres_url(_resolve_supabase_database_url(), "SUPABASE_DATABASE_URL")
    dst_raw = os.environ.get("DATABASE_URL", "").strip()
    dst_url = _validate_postgres_url(dst_raw, "DATABASE_URL")

    src = _connect(src_url, "Supabase")
    dst = _connect(dst_url, "Neon")
    _configure_neon_search_path(dst, dst_raw)

    try:
        with src.cursor() as src_cur, dst.cursor() as dst_cur:
            src_cur.execute(
                """
                SELECT id, email, encrypted_password
                FROM auth.users
                WHERE encrypted_password IS NOT NULL AND email IS NOT NULL
                """
            )
            rows = src_cur.fetchall()
            print(f"Comptes Supabase avec mot de passe : {len(rows)}")

            updated = 0
            skipped = 0
            for uid, email, enc in rows:
                if not enc or not email:
                    skipped += 1
                    continue
                pwd = f"bcrypt:{enc}"
                email_l = email.strip().lower()

                if args.dry_run:
                    dst_cur.execute(
                        "SELECT id, password_hash IS NOT NULL AS has_pwd FROM app_users WHERE id = %s OR email = %s LIMIT 1",
                        (str(uid), email_l),
                    )
                    row = dst_cur.fetchone()
                    if not row:
                        print(f"  [absent Neon] {email_l}")
                    elif row[1]:
                        print(f"  [deja hash] {email_l}")
                    else:
                        print(f"  [a mettre a jour] {email_l}")
                        updated += 1
                    continue

                dst_cur.execute(
                    """
                    UPDATE app_users
                    SET password_hash = %s, updated_at = now()
                    WHERE (id = %s OR email = %s)
                      AND (password_hash IS NULL OR password_hash = '')
                    """,
                    (pwd, str(uid), email_l),
                )
                if dst_cur.rowcount:
                    updated += 1
                    print(f"  OK {email_l}")
                else:
                    dst_cur.execute(
                        "SELECT 1 FROM app_users WHERE email = %s OR id = %s LIMIT 1",
                        (email_l, str(uid)),
                    )
                    if dst_cur.fetchone():
                        skipped += 1
                    else:
                        print(f"  [absent Neon] {email_l}")

            if not args.dry_run:
                dst.commit()
            print(f"\nTermine : {updated} mis a jour, {skipped} deja ok / ignores (dry-run={args.dry_run})")
    finally:
        src.close()
        dst.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
