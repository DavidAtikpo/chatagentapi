#!/usr/bin/env bash
set -euo pipefail
pip install -r requirements.txt
playwright install --with-deps chromium
