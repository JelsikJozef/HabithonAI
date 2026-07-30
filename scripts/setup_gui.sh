#!/usr/bin/env bash
# Setup script for HabithonAI GUI on macOS/Linux
# - Installs Python deps from requirements-gui.txt
# - Downloads spaCy language models via spaCy CLI (avoids flaky direct wheel URLs)
# Usage:
#   bash scripts/setup_gui.sh
# Optional: ensure a venv is activated before running.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Prefer venv python if present, else fall back to python3
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi

# Show environment
echo "[INFO] Using Python: $($PY -V)"
$PY -m pip -V || true

# Make sure pip tooling is recent
echo "[INFO] Upgrading pip/setuptools/wheel ..."
$PY -m pip install -U pip setuptools wheel

# Install base requirements
REQ_FILE="requirements-gui.txt"
if [ ! -f "$REQ_FILE" ]; then
  echo "[ERROR] $REQ_FILE not found at repo root. Aborting." >&2
  exit 1
fi

echo "[INFO] Installing dependencies from $REQ_FILE ..."
$PY -m pip install --no-cache-dir -r "$REQ_FILE"

# Ensure spaCy models are installed via downloader
ensure_spacy_model() {
  local model_pkg="$1"   # e.g., en_core_web_sm
  local version="$2"     # e.g., 3.7.1 or empty
  echo "[INFO] Ensuring spaCy model: ${model_pkg}${version:+==${version}}"
  if $PY - <<PY
import importlib.util, sys
sys.exit(0 if importlib.util.find_spec("$model_pkg") else 1)
PY
  then
    echo "[INFO] Model '$model_pkg' already present."
    return 0
  fi
  # Try exact version first if provided
  if [ -n "$version" ]; then
    if $PY -m spacy download "${model_pkg}==${version}"; then
      return 0
    fi
    echo "[WARN] Exact model version ${model_pkg}==${version} failed; trying latest compatible ..."
  fi
  # Fallback to latest compatible
  $PY -m spacy download "$model_pkg" || true
}

# Pin versions known-good with spaCy 3.7
ensure_spacy_model en_core_web_sm 3.7.1
ensure_spacy_model de_core_news_sm 3.7.0
ensure_spacy_model xx_ent_wiki_sm 3.7.0

echo "[INFO] GUI setup completed successfully."
