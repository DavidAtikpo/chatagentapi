# Déploiement API sur Render (avec Playwright)

Le crawl de sites JavaScript nécessite **Chromium + Playwright**. Sur Render, la méthode fiable est le **déploiement Docker**.

## Option recommandée : Docker

1. Render → votre service API → **Settings**
2. **Environment** → Runtime : **Docker**
3. **Root Directory** : `api` (si le repo contient web + api)
4. **Dockerfile Path** : `./Dockerfile`
5. Redéployer (**Manual Deploy** → Clear build cache & deploy)

L'image `mcr.microsoft.com/playwright/python` inclut déjà Chromium.

## Vérifier après déploiement

Ouvrir :

```
https://VOTRE-API.onrender.com/health
```

Réponse attendue :

```json
{
  "status": "ok",
  "playwright": { "available": true, "error": null }
}
```

Si `"available": false`, consulter `"error"` dans la réponse.

## Option native (non recommandée)

Build command :

```bash
pip install -r requirements.txt && python -m playwright install chromium
```

Start command :

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Souvent **insuffisant** sur Render natif (libs système manquantes). Préférer Docker.

## Mémoire

Chromium headless consomme ~300–500 Mo RAM. Utiliser au minimum le plan **Starter** (512 Mo+) sur Render.
