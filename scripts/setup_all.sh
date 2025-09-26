#!/usr/bin/env bash
# Unified installer for full HabithonAI toolchain (GUI + preprocessing + anonymization + translation + API)
# Usage:
#   bash scripts/setup_all.sh
# or (make executable first):
#   chmod +x scripts/setup_all.sh && ./scripts/setup_all.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

REQ_FILE="requirements-all.txt"
if [ ! -f "$REQ_FILE" ]; then
  echo "[ERROR] $REQ_FILE not found at repo root. Aborting." >&2
  exit 1
fi

echo "[INFO] Installing Python packages from $REQ_FILE ..."
pip install -r "$REQ_FILE"

echo "[INFO] Downloading spaCy models (en, de, multi xx) ..."
python3 -m spacy download en_core_web_sm || true
python3 -m spacy download de_core_news_sm || true
python3 -m spacy download xx_ent_wiki_sm || true

echo "[INFO] Installation complete. Optional steps:"
echo "  - Set ANON_PRESIDIO_LANGS='en:en_core_web_sm,de:de_core_news_sm,sk:xx_ent_wiki_sm' for explicit mapping"
echo "  - For GPU translation, install ctranslate2 / torch with CUDA wheels as needed"
echo "  - Run: PYTHONPATH=src python3 -m gui.app to launch GUI"
