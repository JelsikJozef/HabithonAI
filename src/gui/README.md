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
- Ensure `src` is on PYTHONPATH. In repo root:
  - python3 -m gui.app
  - Or via helper: python3 scripts/run_gui.py

Tabs
- Preprocess: plan and run convert-only Markdown pipeline (folder -> .md).
- Anonymization: detect, pseudonymize (with context), and de-anonymize sample text.
- Jobs: placeholder for history and logs.
- Settings: environment/session settings and preflight.

Service facade
- gui.services.facade.GuiServices exposes small, stable methods used by views:
  - convert_plan(cfg) -> Plan
  - convert_run(cfg) -> RunResult
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
