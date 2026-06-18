# Configuration push (FCM) — handoff conseiller

Les notifications **en arrière-plan** passent par Firebase Cloud Messaging, envoyé par l'API Python.

## Prérequis (déjà faits)

- App Android `com.chatagent.chatagent_mobile` dans Firebase (`ebonservices-75030`)
- `mobile/android/app/google-services.json` présent
- Conseiller connecté dans l'app → token enregistré dans `agent_device_tokens`

## Étape 1 — Télécharger la clé Firebase

1. [Firebase Console](https://console.firebase.google.com/) → projet **ebonservices-75030**
2. ⚙️ **Project settings** → onglet **Service accounts**
3. **Generate new private key** → télécharger le `.json`

## Étape 2 — API locale (dev)

Copiez le fichier téléchargé :

```
api/firebase-service-account.json
```

Redémarrez uvicorn, puis testez :

```powershell
cd api
py scripts/test_push.py
```

Vous devez recevoir **« 🧪 Test ChatAgent »** sur le téléphone du conseiller.

Vérification rapide :

```
GET http://localhost:8000/health
→ { "push_enabled": true, ... }
```

### Script automatique

```powershell
cd api
.\scripts\setup_push.ps1 "C:\Users\Vous\Downloads\ebonservices-75030-firebase-adminsdk-xxxxx.json"
```

## Étape 3 — Render (production)

Dans [Render](https://dashboard.render.com) → service **chatagentapi** → **Environment** :

| Key | Value |
|-----|--------|
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Contenu du JSON **sur une seule ligne** |

Générer la ligne (PowerShell) :

```powershell
(Get-Content api/firebase-service-account.json -Raw) -replace "`n","" | Set-Clipboard
```

Coller dans Render → **Save** → **Manual Deploy**.

Vérifier :

```
https://chatagentapi.onrender.com/health
→ "push_enabled": true
```

## Étape 4 — Test handoff réel

1. Conseiller **Disponible** (Dashboard → Conseillers)
2. App mobile connectée, puis mise en **arrière-plan**
3. Widget : « je veux parler à un conseiller »
4. Notification sur le téléphone

## Dépannage

| Symptôme | Solution |
|----------|----------|
| `push_enabled: false` | Clé Firebase absente ou JSON invalide |
| `Tokens FCM trouvés : 0` | Ouvrir l'app mobile et se connecter |
| Push local OK, pas sur Render | Variable Render + redeploy |
| Pas de notif arrière-plan | `POST_NOTIFICATIONS` autorisé sur Android |
| Conseiller ne reçoit pas | Vérifier site assigné + statut Disponible |
