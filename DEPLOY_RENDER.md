# Déploiement API sur Render (avec Playwright)

Le repo GitHub contient **uniquement le dossier API** (pas de monorepo web+api).
La racine du repo = `requirements.txt`, `Dockerfile`, `app/`, etc.

Le crawl de sites JavaScript nécessite **Chromium + Playwright**. Sur Render, utiliser le **déploiement Docker**.

## Configuration Render (repo API seul)

| Champ | Valeur |
|-------|--------|
| **Root Directory** | *(vide — laisser par défaut)* |
| **Runtime** | **Docker** |
| **Dockerfile Path** | `./Dockerfile` |
| **Docker Command** | *(vide)* |
| **Health Check Path** | `/health` |
| **Instance Type** | **Starter** minimum (512 Mo+) |

### Build & Start

Avec Docker, **ne pas** remplir Build Command ni Start Command — le `Dockerfile` s’en charge :

```dockerfile
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

## Variables d'environnement

Copier depuis `.env.example` dans Render → **Environment** :

- `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_JWT_SECRET`
- `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`
- `CORS_ORIGINS` — URL du dashboard Vercel
- `APP_URL` — URL du site web
- `API_URL` — `https://VOTRE-SERVICE.onrender.com/api/v1`
- `WIDGET_CDN_URL`
- `FIREBASE_SERVICE_ACCOUNT_JSON` — JSON sur une ligne (push mobile)

## Déployer

1. Push sur GitHub (`main`)
2. Render → **Manual Deploy** → **Clear build cache & deploy** (1ère fois en Docker)
3. Attendre la fin du build (~5–10 min)

## Vérifier Playwright

```
https://VOTRE-SERVICE.onrender.com/health
```

Attendu :

```json
{
  "status": "ok",
  "playwright": { "available": true, "error": null }
}
```

Puis relancer **Re-crawler** sur le dashboard.

## Erreurs fréquentes

| Problème | Cause | Solution |
|----------|-------|----------|
| Playwright indisponible | Runtime Python au lieu de Docker | Runtime → **Docker** |
| Dockerfile not found | Root Directory rempli par erreur | Root Directory → **vide** |
| Crawl crash / lent | RAM insuffisante | Plan **Starter** ou plus |
