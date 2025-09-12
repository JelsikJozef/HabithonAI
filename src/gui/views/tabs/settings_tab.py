from __future__ import annotations

import os
from ..qt import (
    QWidget,
    QVBoxLayout,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QTextEdit,
)

from ...services.facade import GuiServices


class SettingsTab(QWidget):
    """Global settings and simple preflight checks (minimal scaffold)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.svc = GuiServices()

        vbox = QVBoxLayout(self)

        form = QFormLayout()
        self.vault_dir = QLineEdit(
            os.getenv("ANON_VAULT_DIR", "src/anonymization/.anonymization_vault")
        )
        form.addRow("ANON_VAULT_DIR:", self.vault_dir)

        self.save_btn = QPushButton("Save env (session)")
        self.preflight_btn = QPushButton("Preflight checks")
        vbox.addLayout(form)
        vbox.addWidget(self.save_btn)
        vbox.addWidget(self.preflight_btn)

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        vbox.addWidget(self.output, 1)

        self.save_btn.clicked.connect(self._on_save)
        self.preflight_btn.clicked.connect(self._on_preflight)

    def _on_save(self) -> None:
        os.environ["ANON_VAULT_DIR"] = self.vault_dir.text().strip()
        self.output.setPlainText("Saved ANON_VAULT_DIR for current process.")

    def _on_preflight(self) -> None:
        lines: list[str] = []
        try:
            detectors, vault = self.svc.anon_build()
            lines.append(f"Detectors: {len(detectors)} (ok)")
            # Touch vault by listing zero mappings for a dummy context if method exists
            base = getattr(vault, "base_dir", "?")
            lines.append(f"Vault backend: {vault.__class__.__name__} base_dir={base}")
        except Exception as e:
            lines.append(f"Anonymization wiring failed: {e}")
        self.output.setPlainText("\n".join(lines))
