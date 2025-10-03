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
    - This installs Presidio + spaCy and the required spaCy language models (en, de, xx) automatically.
  - Or: pip install PySide6
    - If you choose this minimal install, also install Presidio and spaCy:
      - pip install presidio-analyzer spacy
      - And install at least one spaCy model:
        - python3 -m spacy download en_core_web_sm
  - For LLM summaries (Step 3), install:
    - pip install openai>=1.42.0 python-dotenv>=1.0.1

Run (development)
- The GUI package lives under src/gui. Ensure that the repository's src/ directory
  is on PYTHONPATH when you launch the app. From the repo root you can run either:
  - PYTHONPATH=src python3 -m gui.app
  - Or use the provided launcher that prepends src to sys.path automatically:
    python3 scripts/run_gui.py

Quick notes
- The GUI requires PySide6; when not installed the app prints an explanatory message
  and exits with a non-zero code.
- LLM summarization requires OPENAI_API_KEY set in a .env at the repo root (never logged). Only anonymized text is sent to the API.

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
- Background worker + Cancel buttons (all long-running tabs):
  - Convert, Language Detection, Anonymization, Anon Batch, and LLM Summary run in a background thread.
  - Live logs stream to the output pane; Cancel requests a cooperative stop.
- LLM Summary tab (new):
  - Run Step 3 summarization/keywords by document UID (requires Step 1 artifacts in outputs/artifacts/{uid}/step1).
  - Model selection via editable dropdown (defaults to gpt-5-mini; includes common OpenAI models; custom names allowed).
  - Folder mode: run summarization over a folder of anonymized Markdown files (*.anonymized.md by default). For each input file, writes sidecars next to it:
    - <name>.summary.txt
    - <name>.keywords.json
  - Deterministic settings (temperature=0, top_p=1, seed=0). Never logs document content or secrets.

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
- LLM Summary flow:
  - UID mode: paste a document_uid (from Step 1) and click "Run LLM Summary (UID)". On success, "summary.txt" and "keywords.json" appear under outputs/artifacts/{uid}/step3/.
  - Folder mode: select a folder with anonymized .md files and click "Run LLM Summary (Folder)". Sidecars are written next to each matched file: <name>.summary.txt and <name>.keywords.json.
  - The model dropdown is editable; you can type a custom model name if needed. Timeout is configurable (default 60s).

Tabs
- Preprocess: plan and run convert-only Markdown pipeline (folder -> .md) and translation.
- Language Detection: detect the primary language of a document (with candidates/window tuning).
- Anonymization: detect, pseudonymize (with context), and de-anonymize sample text.
- Anon Batch: folder anonymization (deterministic or pseudonymize) with cooperative cancellation.
- LLM Summary: run Step 3 for a document UID or summarize a folder of anonymized Markdown.
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
  - step3_run(document_uid, content_hash?, model?, timeout_s?) -> Step 3 result
  - summarize_folder_run(cfg={src, pattern, overwrite, model?, timeout_s?}) -> batch result

Design choices
- Qt shim: gui.views.qt provides a compatibility layer that imports PySide6 at runtime
  and falls back to stubs so static type/lint checks can run without PySide6 installed.
- Long-running tasks: implemented via a background worker (QThread) with cooperative
  cancellation and log forwarding into the UI.

Packaging (optional)
- macOS app via PyInstaller:
  pyinstaller -n HabithonGUI --onefile -w -p src src/gui/app.py
  - Add data files and model directories as needed.

Next steps
- Persist job history and logs in a small JSON DB under outputs/.
- Add table views for results and open-file shortcuts.
- Add vector-store tab once builder is implemented.
