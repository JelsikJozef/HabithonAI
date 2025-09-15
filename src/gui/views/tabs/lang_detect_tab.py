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
    QToolButton,
    QStyle,
    QCheckBox,
    QComboBox,
)

from ...services.facade import GuiServices


class LanguageDetectTab(QWidget):
    """Detect the language of a single document using the pipeline's detector with advanced routing options."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.svc = GuiServices()

        vbox = QVBoxLayout(self)

        # File selection
        form = QFormLayout()
        self.file_edit = QLineEdit()
        self.file_edit.setPlaceholderText(
            "Select a document (.md, .txt, .docx, .pdf, .xlsx, .msg, .jpg)…"
        )
        pick_btn = QPushButton("Browse…")
        file_row = QHBoxLayout()
        file_row.addWidget(self.file_edit, 1)
        file_row.addWidget(pick_btn)
        form.addRow(
            self._create_label_with_help(
                "Document:",
                "Select a document to analyze. Supports Markdown, text, Word, PDF, Excel, Outlook, and image files.",
            ),
            file_row,
        )

        # Language Detection Settings
        lang_label = QLabel("Language Detection Settings")
        lang_label.setStyleSheet("font-weight: bold; color: #2c5aa0; margin-top: 10px;")
        form.addRow(lang_label)

        # Candidates
        self.candidates_edit = QLineEdit()
        self.candidates_edit.setPlaceholderText("e.g. sk,cs,de,en")
        form.addRow(
            self._create_label_with_help(
                "Candidates:",
                "Optional comma-separated list of language codes to limit detection to. Leave empty to detect from all supported languages.",
            ),
            self.candidates_edit,
        )

        # Detection window controls
        self.max_chars_sp = QSpinBox()
        self.max_chars_sp.setRange(100, 50000)
        self.max_chars_sp.setValue(5000)
        form.addRow(
            self._create_label_with_help(
                "Max chars:",
                "Maximum number of characters to analyze from the document. Larger values give more accurate results but take longer.",
            ),
            self.max_chars_sp,
        )

        self.min_chars_sp = QSpinBox()
        self.min_chars_sp.setRange(10, 1000)
        self.min_chars_sp.setValue(50)
        form.addRow(
            self._create_label_with_help(
                "Min chars:",
                "Minimum number of characters required before trusting detection results. Very short texts are unreliable.",
            ),
            self.min_chars_sp,
        )

        # Detection mode
        self.detection_mode = QComboBox()
        self.detection_mode.addItems(["Basic Detection", "Top-K Analysis", "Advanced Routing"])
        self.detection_mode.setCurrentText("Top-K Analysis")
        form.addRow(
            self._create_label_with_help(
                "Detection mode:",
                "Basic: single language result. Top-K: multiple candidates with scores. Advanced: full routing analysis with validation.",
            ),
            self.detection_mode,
        )

        # Top-K specific controls
        self.topk_k = QSpinBox()
        self.topk_k.setRange(1, 10)
        self.topk_k.setValue(5)
        form.addRow(
            self._create_label_with_help(
                "Top-K candidates:",
                "Number of top language candidates to return with confidence scores.",
            ),
            self.topk_k,
        )

        # Advanced routing controls (only shown when Advanced mode is selected)
        self.routing_label = QLabel("Advanced Routing Analysis")
        self.routing_label.setStyleSheet("font-weight: bold; color: #2c5aa0; margin-top: 10px;")
        form.addRow(self.routing_label)

        # Threshold controls
        self.tau_low = QSpinBox()
        self.tau_low.setRange(1, 100)
        self.tau_low.setValue(70)
        self.tau_low.setSuffix("%")
        form.addRow(
            self._create_label_with_help(
                "Low confidence threshold (τ_low):",
                "Confidence threshold below which the system considers the detection unreliable and may trigger additional analysis.",
            ),
            self.tau_low,
        )

        self.delta_close = QSpinBox()
        self.delta_close.setRange(1, 20)
        self.delta_close.setValue(5)
        self.delta_close.setSuffix("%")
        form.addRow(
            self._create_label_with_help(
                "Close margin threshold (δ_close):",
                "When top two language candidates are within this margin, the detection is considered ambiguous.",
            ),
            self.delta_close,
        )

        # Translation validation controls (for advanced mode)
        self.enable_translation_test = QCheckBox("Enable translation quality test")
        self.enable_translation_test.setChecked(False)
        form.addRow(
            self._create_label_with_help(
                "Translation test:",
                "Perform a micro-translation test to validate language detection accuracy using English confidence and similarity analysis.",
            ),
            self.enable_translation_test,
        )

        self.tau_en = QSpinBox()
        self.tau_en.setRange(1, 100)
        self.tau_en.setValue(90)
        self.tau_en.setSuffix("%")
        form.addRow(
            self._create_label_with_help(
                "English confidence threshold (τ_en):",
                "Minimum English confidence required for translation test validation.",
            ),
            self.tau_en,
        )

        self.similarity_threshold = QSpinBox()
        self.similarity_threshold.setRange(1, 100)
        self.similarity_threshold.setValue(92)
        self.similarity_threshold.setSuffix("%")
        form.addRow(
            self._create_label_with_help(
                "Similarity threshold:",
                "Maximum similarity allowed between source and translation (to detect identity/poor translations).",
            ),
            self.similarity_threshold,
        )

        self.probe_slice = QSpinBox()
        self.probe_slice.setRange(100, 2000)
        self.probe_slice.setValue(600)
        form.addRow(
            self._create_label_with_help(
                "Test slice chars:",
                "Number of characters to use for micro-translation quality tests.",
            ),
            self.probe_slice,
        )

        vbox.addLayout(form)

        # Actions
        actions = QHBoxLayout()
        self.detect_btn = QPushButton("Analyze Language")
        self.detect_btn.setStyleSheet(
            "QPushButton { background-color: #4CAF50; color: white; font-weight: bold; padding: 8px 16px; }"
        )
        actions.addWidget(self.detect_btn)
        actions.addStretch(1)
        vbox.addLayout(actions)

        # Output
        self.result_label = QLabel("Analysis Result:")
        self.result_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setMinimumHeight(200)
        vbox.addWidget(self.result_label)
        vbox.addWidget(self.output, 1)

        # Signals
        pick_btn.clicked.connect(self._browse_file)
        self.detect_btn.clicked.connect(self._on_detect)
        self.detection_mode.currentTextChanged.connect(self._update_enabled_states)
        self.enable_translation_test.toggled.connect(self._update_enabled_states)

        # Initialize enabled/disabled state
        self._update_enabled_states()

    def _create_label_with_help(self, text: str, tooltip: str) -> QWidget:
        """Create a label with a help button that shows tooltip on hover."""
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        label = QLabel(text)
        help_btn = QToolButton()
        help_btn.setText("?")
        help_btn.setToolTip(tooltip)
        help_btn.setStyleSheet(
            """
            QToolButton {
                background-color: #E3F2FD;
                border: 1px solid #2196F3;
                border-radius: 10px;
                color: #1976D2;
                font-weight: bold;
                font-size: 10px;
                min-width: 16px;
                max-width: 16px;
                min-height: 16px;
                max-height: 16px;
            }
            QToolButton:hover {
                background-color: #2196F3;
                color: white;
            }
        """
        )

        layout.addWidget(label)
        layout.addWidget(help_btn)
        layout.addStretch(1)

        return container

    def _update_enabled_states(self) -> None:
        """Update which controls are enabled based on current settings."""
        mode = self.detection_mode.currentText()

        # Top-K controls enabled for Top-K Analysis and Advanced Routing
        topk_enabled = mode in ["Top-K Analysis", "Advanced Routing"]
        self.topk_k.setEnabled(topk_enabled)

        # Advanced routing controls enabled only for Advanced Routing
        advanced_enabled = mode == "Advanced Routing"
        self.routing_label.setEnabled(advanced_enabled)
        self.tau_low.setEnabled(advanced_enabled)
        self.delta_close.setEnabled(advanced_enabled)
        self.enable_translation_test.setEnabled(advanced_enabled)

        # Translation test controls enabled when translation test is checked and advanced mode is on
        test_enabled = advanced_enabled and self.enable_translation_test.isChecked()
        self.tau_en.setEnabled(test_enabled)
        self.similarity_threshold.setEnabled(test_enabled)
        self.probe_slice.setEnabled(test_enabled)

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
        if not path:
            self.output.setPlainText("Please select a document to analyze.")
            return

        # Prepare parameters
        cand_raw = self.candidates_edit.text().strip()
        candidates = (
            [s.strip().lower() for s in cand_raw.split(",") if s.strip()] if cand_raw else None
        )
        max_chars = int(self.max_chars_sp.value())
        min_chars = int(self.min_chars_sp.value())
        mode = self.detection_mode.currentText()

        try:
            if mode == "Basic Detection":
                # Use simple detection
                res: dict[str, Any] = self.svc.lang_detect_file(
                    path,
                    candidates=candidates,
                    max_chars=max_chars,
                    min_chars=min_chars,
                )
                self._display_basic_result(res)

            elif mode == "Top-K Analysis":
                # Use top-k detection
                res = self.svc.lang_detect_topk(
                    path,
                    k=int(self.topk_k.value()),
                    candidates=candidates,
                    max_chars=max_chars,
                    min_chars=min_chars,
                )
                self._display_topk_result(res)

            elif mode == "Advanced Routing":
                # Use advanced routing analysis
                routing_config = {
                    "tau_low": float(self.tau_low.value()) / 100.0,
                    "delta_close": float(self.delta_close.value()) / 100.0,
                }

                if self.enable_translation_test.isChecked():
                    routing_config.update(
                        {
                            "tau_en": float(self.tau_en.value()) / 100.0,
                            "similarity_noop_threshold": float(self.similarity_threshold.value())
                            / 100.0,
                            "probe": {"slice_chars": int(self.probe_slice.value())},
                        }
                    )

                res = self.svc.lang_detect_advanced_routing(
                    path,
                    k=int(self.topk_k.value()),
                    candidates=candidates,
                    max_chars=max_chars,
                    min_chars=min_chars,
                    routing_config=routing_config,
                    enable_translation_test=self.enable_translation_test.isChecked(),
                )
                self._display_advanced_result(res)

        except Exception as e:
            self.output.setPlainText(f"Analysis failed: {e}")

    def _display_basic_result(self, res: dict[str, Any]) -> None:
        """Display basic language detection result."""
        if res.get("error"):
            self.output.setPlainText(str(res.get("error")))
            return

        lines = [
            "=== Basic Language Detection ===",
            f"Language: {res.get('lang')}",
            f"Confidence: {res.get('confidence'):.3f}"
            if isinstance(res.get("confidence"), (int, float))
            else f"Confidence: {res.get('confidence')}",
            f"Engine: {res.get('engine')}",
            f"Characters analyzed: {res.get('chars_used')}",
            f"Path: {res.get('path')}",
        ]
        if res.get("log"):
            lines.append(f"Log: {res.get('log')}")
        self.output.setPlainText("\n".join(lines))

    def _display_topk_result(self, res: dict[str, Any]) -> None:
        """Display top-k language detection result."""
        if res.get("error"):
            self.output.setPlainText(str(res.get("error")))
            return

        lines = [
            "=== Top-K Language Analysis ===",
            f"Primary Language: {res.get('lang_code')}",
            f"Primary Confidence: {res.get('confidence'):.3f}"
            if isinstance(res.get("confidence"), (int, float))
            else f"Primary Confidence: {res.get('confidence')}",
            "",
            "Top Candidates:",
        ]

        topk = res.get("topk", [])
        for i, candidate in enumerate(topk, 1):
            score = candidate.get("score", 0)
            lines.append(
                f"  {i}. {candidate.get('code')} - {candidate.get('name', 'Unknown')} ({score:.3f})"
            )

        flags = res.get("flags", {})
        if flags:
            lines.extend(
                [
                    "",
                    "Detection Flags:",
                    f"  Low confidence: {'Yes' if flags.get('low_confidence') else 'No'}",
                    f"  Close top-2: {'Yes' if flags.get('close_top2') else 'No'}",
                ]
            )

        lines.extend(
            [
                "",
                f"Characters analyzed: {res.get('chars_used')}",
                f"Path: {res.get('path')}",
            ]
        )
        if res.get("log"):
            lines.append(f"Log: {res.get('log')}")

        self.output.setPlainText("\n".join(lines))

    def _display_advanced_result(self, res: dict[str, Any]) -> None:
        """Display advanced routing analysis result."""
        if res.get("error"):
            self.output.setPlainText(str(res.get("error")))
            return

        lines = [
            "=== Advanced Routing Analysis ===",
            f"Primary Language: {res.get('lang_code')}",
            f"Primary Confidence: {res.get('confidence'):.3f}"
            if isinstance(res.get("confidence"), (int, float))
            else f"Primary Confidence: {res.get('confidence')}",
            "",
            "Top Candidates:",
        ]

        topk = res.get("topk", [])
        for i, candidate in enumerate(topk, 1):
            score = candidate.get("score", 0)
            lines.append(
                f"  {i}. {candidate.get('code')} - {candidate.get('name', 'Unknown')} ({score:.3f})"
            )

        # Routing analysis
        routing = res.get("routing", {})
        if routing:
            lines.extend(
                [
                    "",
                    "Routing Analysis:",
                    f"  Probe triggered: {'Yes' if routing.get('probe_triggered') else 'No'}",
                    f"  Selected source: {routing.get('selected_src', 'N/A')}",
                ]
            )
            if "probe_reason" in routing:
                lines.append(f"  Probe reason: {routing.get('probe_reason')}")
            if "translation_test" in routing:
                tt = routing.get("translation_test") or {}
                lines.extend(
                    [
                        f"  EN confidence: {tt.get('en_confidence')}",
                        f"  Similarity: {tt.get('similarity')}",
                        f"  Passed: {'Yes' if tt.get('passed') else 'No'}",
                    ]
                )

        lines.extend(
            [
                "",
                f"Characters analyzed: {res.get('chars_used')}",
                f"Path: {res.get('path')}",
            ]
        )
        if res.get("log"):
            lines.append(f"Log: {res.get('log')}")

        self.output.setPlainText("\n".join(lines))
