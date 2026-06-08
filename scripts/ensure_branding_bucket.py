"""Create the public `branding` storage bucket (migration 006) if missing."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / "web" / ".env.local")
load_dotenv(ROOT / "api" / ".env")

BUCKET = "branding"
MIME_TYPES = ["image/png", "image/jpeg", "image/webp", "image/svg+xml", "image/gif"]
MAX_BYTES = 2 * 1024 * 1024


def main() -> int:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("SUPABASE_URL et SUPABASE_SERVICE_KEY requis dans api/.env")
        return 1

    from supabase import create_client

    sb = create_client(url, key)
    try:
        sb.storage.get_bucket(BUCKET)
        print(f"Bucket '{BUCKET}' existe déjà.")
        return 0
    except Exception:
        pass

    sb.storage.create_bucket(
        BUCKET,
        options={
            "public": True,
            "file_size_limit": MAX_BYTES,
            "allowed_mime_types": MIME_TYPES,
        },
    )
    print(f"Bucket '{BUCKET}' créé (public, max 2 Mo).")
    print(
        "Pour la politique RLS de lecture, exécutez aussi "
        "supabase/migrations/006_branding_storage.sql dans le SQL Editor."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
