from __future__ import annotations

from ..qt import QWidget, QVBoxLayout, QLabel


class JobsTab(QWidget):
    """Placeholder for job history and logs.

    Future: show live progress, past runs, and open output/report shortcuts.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        vbox = QVBoxLayout(self)
        vbox.addWidget(QLabel("Job history will appear here (coming soon)."))
        vbox.addStretch(1)
