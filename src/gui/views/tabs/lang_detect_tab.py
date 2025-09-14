from __future__ import annotations

from typing import Any

from ..qt import (
    QWidget,
    QVBoxLayout,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QTextEdit,
    QLabel,
    QSpinBox,
)

from ...services.facade import GuiServices


class LanguageDetectTab(QWidget):
    """Detect the language of a single document using the pipeline's detector."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.svc = GuiServices()

        vbox = QVBoxLayout(self)

        # Inputs
        form = QFormLayout()
        self.file_edit = QLineEdit()
        self.file_edit.setPlaceholderText(
            "Select a document (.md, .txt, .docx, .pdf, .xlsx, .msg, .jpg)…"
        )
        pick_btn = QPushButton("Browse…")
        row = QHBoxLayout()
        row.addWidget(self.file_edit, 1)
        row.addWidget(pick_btn)
        form.addRow("Document:", row)

        self.candidates_edit = QLineEdit()
        self.candidates_edit.setPlaceholderText(
            "Optional: limit to candidates, e.g. 'sk,de,cs,pl,hu,en'"
        )
        form.addRow("Candidates:", self.candidates_edit)

        # New: window size controls
        self.max_chars_sp = QSpinBox()
        self.max_chars_sp.setRange(100, 50000)
        self.max_chars_sp.setValue(5000)
        self.min_chars_sp = QSpinBox()
        self.min_chars_sp.setRange(10, 1000)
        self.min_chars_sp.setValue(50)
        form.addRow("Max chars:", self.max_chars_sp)
        form.addRow("Min chars:", self.min_chars_sp)

        vbox.addLayout(form)

        # Actions
        actions = QHBoxLayout()
        self.detect_btn = QPushButton("Detect Language")
        actions.addWidget(self.detect_btn)
        actions.addStretch(1)
        vbox.addLayout(actions)

        # Output
        self.result_label = QLabel("Result:")
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        vbox.addWidget(self.result_label)
        vbox.addWidget(self.output, 1)

        # Signals
        pick_btn.clicked.connect(self._browse_file)
        self.detect_btn.clicked.connect(self._on_detect)

    def _browse_file(self) -> None:
        path, _flt = QFileDialog.getOpenFileName(
            self,
            "Select a document",
            "",
            "Documents (*.md *.markdown *.txt *.docx *.pdf *.xlsx *.msg *.jpg *.jpeg);;All files (*)",
        )
        if path:
            self.file_edit.setText(path)

    def _on_detect(self) -> None:
        path = self.file_edit.text().strip()
        cand_raw = self.candidates_edit.text().strip()
        candidates = (
            [s.strip().lower() for s in cand_raw.split(",") if s.strip()] if cand_raw else None
        )
        max_chars = int(self.max_chars_sp.value())
        min_chars = int(self.min_chars_sp.value())
        if not path:
            self.output.setPlainText("Please select a document to analyze.")
            return
        try:
            res: dict[str, Any] = self.svc.lang_detect_file(
                path,
                candidates=candidates,
                max_chars=max_chars,
                min_chars=min_chars,
            )
            if res.get("error"):
                self.output.setPlainText(str(res.get("error")))
                return
            lang = res.get("lang")
            conf = res.get("confidence")
            eng = res.get("engine")
            used = res.get("chars_used")
            lines = [
                f"Language: {lang}",
                f"Confidence: {conf:.3f}"
                if isinstance(conf, (int, float))
                else f"Confidence: {conf}",
                f"Engine: {eng}",
                f"Characters analyzed: {used}",
                f"Path: {res.get('path')}",
            ]
            self.output.setPlainText("\n".join(lines))
        except Exception as e:
            self.output.setPlainText(f"Detection failed: {e}")
