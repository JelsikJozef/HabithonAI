from __future__ import annotations

from ..qt import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLineEdit,
    QTextEdit,
    QPushButton,
    QComboBox,
    QLabel,
)

from ...services.facade import GuiServices


class AnonymizationTab(QWidget):
    """Interactive playground for detection/pseudonymization/de-anonymization."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.svc = GuiServices()

        vbox = QVBoxLayout(self)

        # Inputs
        form = QFormLayout()
        self.lang_combo = QComboBox()
        self.lang_combo.setEditable(True)
        self.lang_combo.addItems(["", "en", "sk", "de", "cs", "pl", "hu"])  # quick presets
        self.ctx_edit = QLineEdit()
        self.ctx_edit.setPlaceholderText("Context ID (required for pseudo/deanonymize)")
        form.addRow("Language:", self.lang_combo)
        form.addRow("Context ID:", self.ctx_edit)
        vbox.addLayout(form)

        self.input_text = QTextEdit()
        self.input_text.setPlaceholderText("Paste or type sample text containing PII…")
        vbox.addWidget(self.input_text, 1)

        # Actions
        actions = QHBoxLayout()
        self.detect_btn = QPushButton("Detect")
        self.pseudo_btn = QPushButton("Pseudonymize")
        self.de_btn = QPushButton("De-anonymize")
        actions.addWidget(self.detect_btn)
        actions.addWidget(self.pseudo_btn)
        actions.addWidget(self.de_btn)
        actions.addStretch(1)
        vbox.addLayout(actions)

        # Output
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        vbox.addWidget(self.output, 1)

        # Wire
        self.detect_btn.clicked.connect(self._on_detect)
        self.pseudo_btn.clicked.connect(self._on_pseudo)
        self.de_btn.clicked.connect(self._on_de)

    def _lang(self) -> str | None:
        s = self.lang_combo.currentText().strip()
        return s or None

    def _ctx(self) -> str:
        return self.ctx_edit.text().strip()

    def _on_detect(self) -> None:
        try:
            text = self.input_text.toPlainText()
            res = self.svc.anon_detect(text, language=self._lang())
            n = len(res.get("entities", []))
            self.output.setPlainText(f"Detected entities: {n}\n\n{res}")
        except Exception as e:
            self.output.setPlainText(f"Detect failed: {e}")

    def _on_pseudo(self) -> None:
        ctx = self._ctx()
        if not ctx:
            self.output.setPlainText("Context ID is required for pseudonymize.")
            return
        try:
            text = self.input_text.toPlainText()
            res = self.svc.anon_pseudonymize(text, context_id=ctx, language=self._lang())
            m = len(res.get("mappings", []))
            self.output.setPlainText(f"Pseudonymized ({m} mappings).\n\n{res}")
        except Exception as e:
            self.output.setPlainText(f"Pseudonymize failed: {e}")

    def _on_de(self) -> None:
        ctx = self._ctx()
        if not ctx:
            self.output.setPlainText("Context ID is required for de-anonymize.")
            return
        try:
            text = self.input_text.toPlainText()
            res = self.svc.anon_deanonymize(text, context_id=ctx)
            self.output.setPlainText(f"Restored text:\n\n{res.get('restored_text', '')}\n\n{res}")
        except Exception as e:
            self.output.setPlainText(f"De-anonymize failed: {e}")
