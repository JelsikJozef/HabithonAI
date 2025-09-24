Habithon GUI (PySide6)

Overview
- Desktop GUI client for Habithon, built with PySide6 (Qt for Python).
- Architecture mirrors clean layers:
  - presentation: gui.views (Qt widgets), gui.app (entry)
  - app: gui.services (facade wrapping existing app-layer functions)
  - adapters: provided by existing packages (preprocessing/adapters, anonymization/adapters)
  - domain: existing domain models/ports
- Offline-friendly; runs in-process, no local web server.

Install (GUI-only)
- From repo root:
  - pip install -r requirements-gui.txt
  - Or: pip install PySide6

Run (development)
- The GUI package lives under src/gui. Ensure that the repository's src/ directory
  is on PYTHONPATH when you launch the app. From the repo root you can run either:
  - PYTHONPATH=src python3 -m gui.app
  - Or use the provided launcher that prepends src to sys.path automatically:
    python3 scripts/run_gui.py

Quick notes
- The GUI requires PySide6; when not installed the app prints an explanatory message
  and exits with a non-zero code.
- If you prefer a native wrapper or entry-point, consider adding a console script
  in pyproject.toml or a small shell wrapper that sets PYTHONPATH for you.

New UI features
- Standalone translation controls (Preprocess tab):
  - Make English variant: request English Markdown creation.
  - Translate-only: skip convert and translate existing Markdown under Source.
  - Translator: choose engine (auto, marian_opus, ct2_nllb).
  - Advanced routing controls with helpful tooltip buttons (?) for all parameters.
- Language detection tuning (unified across tabs):
  - Preprocess tab: Candidates, max chars, min chars with helpful tooltips.
  - Language Detection tab: Multiple analysis modes with advanced routing options.
  - Environment overrides honored globally: HABITHON_LANGID_MAX_CHARS, HABITHON_LANGID_MIN_CHARS.
- Advanced routing system (model-only):
  - Intelligent probe selection when language detection is ambiguous.
  - Quality validation using English confidence and similarity analysis.
  - Deterministic retry ladder across engines and source languages.
  - Comprehensive routing telemetry in translation reports.
  - Helpful (?) tooltip buttons explaining each parameter's purpose.

Advanced routing features
- Preprocess tab: Full integration with translation workflow
  - Enable/disable advanced routing with smart UI state management.
  - Configurable thresholds: τ_low (low confidence), δ_close (close margin), τ_en (English threshold).
  - Near-identity detection and probe settings with helpful explanations.
  - Real-time parameter validation and environment variable injection.
- Language Detection tab: Three analysis modes
  - Basic Detection: Simple language identification.
  - Top-K Analysis: Multiple candidates with confidence scores and detection flags.
  - Advanced Routing: Full routing analysis with probe simulation and translation quality tests.
  - Interactive controls with live enable/disable state management.
  - Detailed result display showing routing decisions and validation outcomes.

Usage tips
- Convert flow: set Source/Output and options, click Plan to preview, then Run.
- Translate flow: set Source/Output, choose translation options, click Translate.
  - Translate-only translates .md files under Source; otherwise it uses results from the last convert run.
  - The report path is shown after a run; open it to inspect per-file outcomes.

Tabs
- Preprocess: plan and run convert-only Markdown pipeline (folder -> .md) and translation.
- Language Detection: detect the primary language of a document (with candidates/window tuning).
- Anonymization: detect, pseudonymize (with context), and de-anonymize sample text.
- Jobs: placeholder for history and logs.
- Settings: environment/session settings and preflight.

## Anonymization tab

Fields:
- Language: Optional hint to detectors (e.g., en, sk, de).
- Context ID: Required for pseudonymize/de-anonymize and deterministic anonymize; identifies the mapping set in the vault.
- Tenant ID: Optional scope for deterministic anonymize; influences HMAC token derivation and helps separate domains.

Actions:
- Detect: Runs configured detectors and shows merged PII entities.
- Pseudonymize: Replaces PII with robust tokens like `{{PII:TYPE:i:xxxx}}` and saves mappings to the vault.
- De-anonymize: Restores original values using mappings from the vault for the given Context ID.
- Deterministic anonymize: Replaces PII with deterministic HMAC tokens (h:<kid>:<hex>) using current keyset; saves mappings (token->value) for authorized restoration.

Notes:
- By default, detectors use Presidio if available; otherwise fall back to an offline regex detector. You can force regex with `ANON_DETECTORS=regex`.
- File-based vault by default; override with `ANON_VAULT_DIR` or use Postgres by setting `ANON_POSTGRES_DSN`.
- Keys for deterministic anonymize come from `ANON_KEYSET` JSON; a built-in test key is used if not provided. For production, configure a proper keyset.

Service facade
- gui.services.facade.GuiServices exposes small, stable methods used by views:
  - convert_plan(cfg) -> Plan
  - convert_run(cfg) -> RunResult
  - translate_run(cfg, make_english, translate_only, translator?) -> {code, report}
  - lang_detect_file(file, candidates?, max_chars?, min_chars?) -> {lang, confidence, engine}
  - anon_detect(text, language)
  - anon_pseudonymize(text, context_id, language)
  - anon_deanonymize(text, context_id)

Design choices
- Qt shim: gui.views.qt provides a compatibility layer that imports PySide6 at runtime
  and falls back to stubs so static type/lint checks can run without PySide6 installed.
- Long-running tasks: current scaffold calls services synchronously; next iterations
  should move calls to worker threads (QThread/QtConcurrent) with progress.

Packaging (optional)
- macOS app via PyInstaller:
  pyinstaller -n HabithonGUI --onefile -w -p src src/gui/app.py
  - Add data files and model directories as needed.

Next steps
- Wire progress and cancellable jobs; store history in a small JSON DB under outputs/.
- Add table views for results and open-file shortcuts.
- Add vector-store tab once builder is implemented.
