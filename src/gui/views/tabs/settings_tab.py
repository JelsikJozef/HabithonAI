from __future__ import annotations

import os
from ..qt import (
    QWidget,
    QVBoxLayout,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QComboBox,
)
from ..ui_helpers import create_field_label, section_header
from ..theme import apply_theme

from ...services.facade import GuiServices


class SettingsTab(QWidget):
    """Global settings and simple preflight checks + theme selection."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.svc = GuiServices()

        vbox = QVBoxLayout(self)

        form = QFormLayout()
        # Vault dir
        self.vault_dir = QLineEdit(
            os.getenv("ANON_VAULT_DIR", "src/anonymization/.anonymization_vault")
        )
        form.addRow(
            create_field_label(
                "ANON_VAULT_DIR:", "Directory used by file token vault (set ANON_VAULT_DIR)"
            ),
            self.vault_dir,
        )

        # Theme selection
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["system", "light", "dark"])
        cur_env = os.getenv("HABITHON_GUI_THEME")
        if cur_env and cur_env in {"light", "dark"}:
            self.theme_combo.setCurrentText(cur_env)
        form.addRow(
            create_field_label(
                "Theme:",
                "Select light/dark or system auto-detect (writes HABITHON_GUI_THEME for session).",
            ),
            self.theme_combo,
        )

        vbox.addLayout(form)
        vbox.addWidget(section_header("Actions"))

        self.save_btn = QPushButton("Save settings (session)")
        self.preflight_btn = QPushButton("Preflight checks")
        self.apply_theme_btn = QPushButton("Apply theme now")
        vbox.addWidget(self.save_btn)
        vbox.addWidget(self.apply_theme_btn)
        vbox.addWidget(self.preflight_btn)

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("Settings output / diagnostics…")
        vbox.addWidget(self.output, 1)

        self.save_btn.clicked.connect(self._on_save)
        self.preflight_btn.clicked.connect(self._on_preflight)
        self.apply_theme_btn.clicked.connect(self._on_apply_theme)

    # --- Handlers ---------------------------------------------------------
    def _on_save(self) -> None:
        os.environ["ANON_VAULT_DIR"] = self.vault_dir.text().strip()
        choice = self.theme_combo.currentText().strip().lower()
        if choice == "system":
            os.environ.pop("HABITHON_GUI_THEME", None)
        else:
            os.environ["HABITHON_GUI_THEME"] = choice
        self.output.setPlainText(
            "Saved settings for current process (environment variables updated)."
        )

    def _on_apply_theme(self) -> None:
        choice = self.theme_combo.currentText().strip().lower()
        from ..qt import QApplication

        app = QApplication.instance()
        if not app:
            self.output.setPlainText("No QApplication instance available.")
            return
        force = None if choice == "system" else choice
        mode = apply_theme(app, force=force)
        self.output.append(f"Applied theme: {mode}")

    def _on_preflight(self) -> None:
        lines: list[str] = []
        try:
            detectors, vault = self.svc.anon_build()
            lines.append(f"Detectors: {len(detectors)} (ok)")
            base = getattr(vault, "base_dir", getattr(vault, "dsn", "?"))
            lines.append(f"Vault backend: {vault.__class__.__name__} base={base}")
        except Exception as e:
            lines.append(f"Anonymization wiring failed: {e}")
        self.output.setPlainText("\n".join(lines))
