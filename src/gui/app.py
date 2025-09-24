from __future__ import annotations

"""GUI entry point for the Habithon desktop client (PySide6).

This file wires the main window and starts the Qt event loop.
Run locally (dev):
    python -m gui.app

Assumptions:
- PySide6 is installed in the active environment.
- The project is executed with `src` on PYTHONPATH (e.g., via IDE or `PYTHONPATH=src`).
"""

import sys, os
from .views.qt import QApplication, QT_AVAILABLE

from .views.main_window import MainWindow
from .views.theme import apply_theme  # unified theme


def main() -> int:
    if not QT_AVAILABLE:
        msg = (
            "PySide6 (Qt) isn’t installed. Install GUI deps and re-run:\n"
            "  pip install -r requirements-gui.txt\n"
            "Then launch:\n"
            "  PYTHONPATH=src python3 -m gui.app\n"
        )
        print(msg, file=sys.stderr)
        return 2

    app = QApplication(sys.argv)
    app.setApplicationName("Habithon GUI")
    app.setOrganizationName("HabithonAI")

    # Apply theme (auto-detect unless overridden by HABITHON_GUI_THEME=light|dark)
    forced = os.environ.get("HABITHON_GUI_THEME")
    if forced not in {None, "light", "dark"}:
        forced = None
    apply_theme(app, force=forced)

    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
