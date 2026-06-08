"""
Supprime toutes les données métier (tables public.*).
Ne supprime PAS les comptes auth (auth.users) — à faire manuellement dans Supabase si besoin.

Usage: py api/scripts/wipe_database.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / "web" / ".env.local")
load_dotenv(ROOT / "api" / ".env")

# Ordre : enfants avant parents
TABLES = [
    "messages",
    "knowledge_chunks",
    "traffic_links",
    "widget_click_events",
    "leads",
    "conversations",
    "visitors",
    "notifications",
    "sites",
    "organizations",
]

# PostgREST exige un filtre sur DELETE
DELETE_FILTER = ("id", "00000000-0000-0000-0000-000000000000")


def wipe_table(sb, table: str) -> int:
    try:
        before = sb.table(table).select("id", count="exact").limit(0).execute()
        count = before.count or 0
        if count == 0:
            print(f"  {table}: vide")
            return 0
        sb.table(table).delete().neq(DELETE_FILTER[0], DELETE_FILTER[1]).execute()
        print(f"  {table}: {count} ligne(s) supprimée(s)")
        return count
    except Exception as e:
        msg = str(e)
        if "Could not find the table" in msg or "does not exist" in msg:
            print(f"  {table}: ignoré (table absente)")
            return 0
        raise


def wipe_storage(sb) -> None:
    try:
        items = sb.storage.from_("branding").list("")
        if not items:
            print("  storage/branding: vide")
            return
        paths: list[str] = []
        for item in items:
            name = item.get("name") or ""
            if not name:
                continue
            nested = sb.storage.from_("branding").list(name)
            if nested:
                for f in nested:
                    fname = f.get("name") or ""
                    if fname:
                        paths.append(f"{name}/{fname}")
            else:
                paths.append(name)
        if paths:
            sb.storage.from_("branding").remove(paths)
        print(f"  storage/branding: {len(paths)} fichier(s) supprimé(s)")
    except Exception as e:
        print(f"  storage/branding: {e}")


def main() -> int:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("SUPABASE_URL et SUPABASE_SERVICE_KEY requis")
        return 1

    if os.environ.get("CONFIRM_WIPE") != "yes":
        print("Pour confirmer: CONFIRM_WIPE=yes py api/scripts/wipe_database.py")
        return 1

    from supabase import create_client

    sb = create_client(url, key)
    total = 0
    print("Suppression des données...")
    for table in TABLES:
        total += wipe_table(sb, table)
    print("Suppression des fichiers storage...")
    wipe_storage(sb)
    print(f"Terminé ({total} lignes tables public).")
    print("Les comptes login (auth.users) ne sont pas supprimés.")
    print("Supabase > Authentication > Users pour les effacer si besoin.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
