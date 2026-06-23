#!/usr/bin/env bash
# Build natif Render (secours) — préférer Dockerfile pour Playwright.
set -euo pipefail
pip install -r requirements.txt
python -m playwright install chromium
