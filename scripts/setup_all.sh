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

# Helper to check if a spaCy model package is importable; if not, install via spacy downloader.
check_spacy_model() {
  local model="$1"
  python3 - <<PY
import importlib.util, sys
mod = "$model"
sys.exit(0 if importlib.util.find_spec(mod) is not None else 1)
PY
}

maybe_download_model() {
  local model="$1"
  if check_spacy_model "$model"; then
    echo "[INFO] spaCy model '$model' already installed."
  else
    echo "[INFO] Downloading spaCy model '$model' via spacy downloader ..."
    python3 -m spacy download "$model" || true
  fi
}

echo "[INFO] Ensuring spaCy models (en, de, xx) are present ..."
maybe_download_model en_core_web_sm
maybe_download_model de_core_news_sm
maybe_download_model xx_ent_wiki_sm

echo "[INFO] Installation complete. Optional steps:"
echo "  - Set ANON_PRESIDIO_LANGS='en:en_core_web_sm,de:de_core_news_sm,sk:xx_ent_wiki_sm' for explicit mapping"
echo "  - For GPU translation, install ctranslate2 / torch with CUDA wheels as needed"
echo "  - Run: PYTHONPATH=src python3 -m gui.app to launch GUI"
