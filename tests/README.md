Offline test suite for HabithonAI preprocessing → English translation

Overview
- Pytest-based tests organized under tests/unit, tests/integration, and tests/e2e.
- Deterministic, offline-only. No external network calls.
- Golden snapshots for Markdown structure preservation.

Prereqs
- Python 3.11+
- pytest installed. Optional: pytest-xdist.
- No extra ML libraries required; integration tests avoid heavy model loading.

Fixtures
- tests/fixtures/md/*.md: small Markdown samples.
- tests/fixtures/glossary/test_glossary.sqlite: initialized by tests when needed.
- tests/fixtures/cache/: created on the fly.

Env guards
- Tests set TRANSFORMERS_OFFLINE=1 and HF_HUB_OFFLINE=1 to prevent downloads.

Commands
- Run all: pytest -q
- Integration only: pytest -q -m integration
- Update snapshots: pytest -q --snapshot-update
- Coverage: coverage run -m pytest && coverage html

Notes
- CT2/NLLB and Marian adapters are tested only along offline code paths that don’t require the heavy libraries (e.g., “no segments” or cache hits). Real model inference is intentionally excluded.
- Detected outputs normalized to LF and UTF-8.

