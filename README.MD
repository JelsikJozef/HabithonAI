# HabithonAI Monorepo

This repo hosts modules for offline document preprocessing (convert to Markdown, translate to English), anonymization, enrichment, and vector store tooling. Everything is designed for deterministic, offline use.

- Preprocessing (convert ➜ English Markdown): see src/preprocessing/README.md
- GUI: minimal wrapper for local runs
- Vector store: adapters and app for building/searching collections

Quickstart (preprocessing CLI)

```bash
# Convert files under ./in ➜ Markdown under ./out
mdify --src ./in --out ./out --workers 2 --skip-existing

# Convert + make English variant (offline), using NLLB via CTranslate2
mdify --src ./in --out ./out \
  --make-english --translator ct2_nllb --workers 4

# Translate-only: take existing Markdown under ./out and add English variant
mdify --src ./out --out ./out \
  --translate-only --make-english --translator marian_opus --workers 2

# New: tune language detection window and candidates (helps mixed SK/DE corpora)
mdify --src ./in --out ./out \
  --make-english --translator ct2_nllb \
  --lang-candidates sk,de,cs,en --lang-max-chars 20000 --lang-min-chars 80
```

Highlights
- Offline by default: no network calls at runtime. Models and caches are local.
- Language detection before translation (fastText lid.176.bin) ensures adapters get an explicit src_lang.
- Structure-preserving Markdown translation: code blocks, link targets, image URLs, and table layout stay intact.
- New: tunable language detection
  - CLI flags: --lang-max-chars, --lang-min-chars, --lang-candidates
  - Env: HABITHON_LANGID_MAX_CHARS, HABITHON_LANGID_MIN_CHARS
  - GUI controls in Preprocess and Language Detection tabs

Docs
- Preprocessing (CLI, settings, engines, troubleshooting): src/preprocessing/README.md

License
- MIT
