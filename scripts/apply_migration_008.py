"""Apply migration 008 (traffic_links.image_url) when DIRECT_URL or DATABASE_URL is set."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / "web" / ".env.local")
load_dotenv(ROOT / "api" / ".env")

SQL = (ROOT / "supabase" / "migrations" / "008_traffic_link_image.sql").read_text(encoding="utf-8")


def main() -> int:
    url = os.environ.get("DIRECT_URL") or os.environ.get("DATABASE_URL")
    if not url or "[YOUR-PASSWORD]" in url:
        print(
            "DIRECT_URL ou DATABASE_URL manquant dans web/.env.local.\n"
            "Supabase → Settings → Database → copiez l'URL session pooler (port 5432),\n"
            "ou exécutez ce SQL dans le SQL Editor :\n\n"
            f"{SQL.strip()}\n"
        )
        return 1

    try:
        import psycopg2
    except ImportError:
        print("Installez psycopg2 : py -m pip install psycopg2-binary")
        return 1

    conn = psycopg2.connect(url)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(SQL)
        print("Migration 008 appliquée : traffic_links.image_url")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
