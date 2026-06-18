"""Teste l'envoi FCM vers les tokens enregistrés.

Usage (depuis api/) :
  py scripts/test_push.py
  py scripts/test_push.py --org ccef25cb-84d7-4dc0-bb06-da41832bda16
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.services.push_notifications import get_org_agent_tokens, notify_org_handoff
from app.services.supabase_client import get_supabase


async def main() -> int:
    parser = argparse.ArgumentParser(description="Test push FCM handoff")
    parser.add_argument("--org", help="organization_id (défaut : première org)")
    parser.add_argument("--site", help="site_id optionnel pour filtrer les conseillers")
    args = parser.parse_args()

    if not settings.firebase_enabled:
        print("ERREUR: Firebase non configuré.")
        print()
        print("1. Firebase Console -> ebonservices-75030 -> Service accounts -> Generate new private key")
        print("2. Copiez le fichier dans : api/firebase-service-account.json")
        print("   OU ajoutez FIREBASE_SERVICE_ACCOUNT_JSON dans api/.env")
        print("3. Relancez ce script")
        return 1

    org_id = args.org
    if not org_id:
        sb = get_supabase()
        rows = sb.table("organizations").select("id, name").limit(1).execute()
        if not rows.data:
            print("Aucune organisation trouvée.")
            return 1
        org_id = rows.data[0]["id"]
        print(f"Organisation : {rows.data[0].get('name')} ({org_id})")

    tokens = get_org_agent_tokens(org_id, site_id=args.site)
    print(f"Tokens FCM trouvés : {len(tokens)}")
    if not tokens:
        print("Aucun token — connectez-vous à l'app mobile conseiller d'abord.")
        return 1

    sent = await notify_org_handoff(
        org_id,
        title="🧪 Test ChatAgent",
        body="Push configuré avec succès — handoff opérationnel",
        data={"type": "handoff_request", "conversation_id": "test", "reason": "test"},
        site_id=args.site,
    )
    print(f"Notifications envoyées : {sent}/{len(tokens)}")
    return 0 if sent > 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
