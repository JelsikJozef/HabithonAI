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
    QCheckBox,
    QComboBox,
)
from ..ui_helpers import (
    section_header,
    create_field_label,
    auto_expand_combo,
    wrap_with_help,
)  # updated imports

from ...services.facade import GuiServices
from ...services.async_worker import start_worker  # NEW


class LanguageDetectTab(QWidget):
    """Detect the language of a single document using the pipeline's detector with advanced routing options."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.svc = GuiServices()
        self._jobs: list[tuple] = []  # keep thread/worker refs
        self._current: tuple | None = None  # (thread, worker)

        vbox = QVBoxLayout(self)

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
            create_field_label(
                "Document:",
                "Select a document to analyze. Supports Markdown, text, Word, PDF, Excel, Outlook, and image files.",
            ),
            file_row,
        )

        # Language Detection Settings header
        form.addRow(section_header("Language Detection Settings"))

        # Candidates
        self.candidates_edit = QLineEdit()
        self.candidates_edit.setPlaceholderText("e.g. sk,cs,de,en")
        form.addRow(
            create_field_label(
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
            create_field_label(
                "Max chars:",
                "Maximum number of characters to analyze from the document. Larger values give more accurate results but take longer.",
            ),
            self.max_chars_sp,
        )

        self.min_chars_sp = QSpinBox()
        self.min_chars_sp.setRange(10, 1000)
        self.min_chars_sp.setValue(50)
        form.addRow(
            create_field_label(
                "Min chars:",
                "Minimum number of characters required before trusting detection results. Very short texts are unreliable.",
            ),
            self.min_chars_sp,
        )

        # Detection mode
        self.detection_mode = QComboBox()
        self.detection_mode.addItems(["Basic Detection", "Top-K Analysis", "Advanced Routing"])
        self.detection_mode.setCurrentText("Top-K Analysis")
        auto_expand_combo(self.detection_mode)
        form.addRow(
            create_field_label(
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
            create_field_label(
                "Top-K candidates:",
                "Number of top language candidates to return with confidence scores.",
            ),
            self.topk_k,
        )

        # Advanced routing controls header (only active in advanced mode)
        self.routing_header = section_header("Advanced Routing Analysis")
        form.addRow(self.routing_header)

        # Threshold controls
        self.tau_low = QSpinBox()
        self.tau_low.setRange(1, 100)
        self.tau_low.setValue(70)
        self.tau_low.setSuffix("%")
        form.addRow(
            create_field_label(
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
            create_field_label(
                "Close margin threshold (δ_close):",
                "When top two language candidates are within this margin, the detection is considered ambiguous.",
            ),
            self.delta_close,
        )

        # Translation validation controls (advanced mode)
        self.enable_translation_test = QCheckBox("Enable translation quality test")
        self.enable_translation_test.setChecked(False)
        form.addRow(
            create_field_label(
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
            create_field_label(
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
            create_field_label(
                "Similarity threshold:",
                "Maximum similarity allowed between source and translation (to detect identity/poor translations).",
            ),
            self.similarity_threshold,
        )

        self.probe_slice = QSpinBox()
        self.probe_slice.setRange(100, 2000)
        self.probe_slice.setValue(600)
        form.addRow(
            create_field_label(
                "Test slice chars:",
                "Number of characters to use for micro-translation quality tests.",
            ),
            self.probe_slice,
        )

        vbox.addLayout(form)

        # Actions
        actions = QHBoxLayout()
        self.detect_btn = QPushButton("Analyze Language")
        actions.addWidget(
            wrap_with_help(
                self.detect_btn, "Run selected detection mode and display structured results."
            )
        )
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setToolTip("Request cancellation of the current analysis.")
        self.cancel_btn.setEnabled(False)
        actions.addWidget(self.cancel_btn)
        actions.addStretch(1)
        vbox.addLayout(actions)

        # Output
        self.result_label = section_header("Analysis Result")
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setMinimumHeight(200)
        vbox.addWidget(self.result_label)
        vbox.addWidget(self.output, 1)

        # Signals
        pick_btn.clicked.connect(self._browse_file)
        self.detect_btn.clicked.connect(self._on_detect)
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.detection_mode.currentTextChanged.connect(self._update_enabled_states)
        self.enable_translation_test.toggled.connect(self._update_enabled_states)

        # Initialize enabled/disabled state
        self._update_enabled_states()

    def cancel_all_jobs(self) -> None:
        """Cancel and wait for all active background jobs (app shutdown safety)."""
        try:
            if self._current and len(self._current) == 2:
                _t, w = self._current
                try:
                    w.cancel()
                except Exception:
                    pass
        except Exception:
            pass
        for t, w in list(self._jobs):
            try:
                w.cancel()
            except Exception:
                pass
        for t, w in list(self._jobs):
            try:
                t.quit()
                t.wait()
            except Exception:
                pass
        try:
            self.cancel_btn.setEnabled(False)
        except Exception:
            pass

    # --- State management -------------------------------------------------
    def _set_busy(self, busy: bool) -> None:
        for w in [
            self.file_edit,
            self.candidates_edit,
            self.max_chars_sp,
            self.min_chars_sp,
            self.detection_mode,
            self.topk_k,
            self.tau_low,
            self.delta_close,
            self.enable_translation_test,
            self.tau_en,
            self.similarity_threshold,
            self.probe_slice,
            self.detect_btn,
        ]:
            try:
                w.setEnabled(not busy)
            except Exception:
                pass
        try:
            self.cancel_btn.setEnabled(busy)
        except Exception:
            pass

    def _append_log(self, line: str) -> None:
        try:
            prev = self.output.toPlainText()
            nl = "\n" if prev else ""
            self.output.setPlainText(prev + nl + str(line))
        except Exception:
            pass

    def _update_enabled_states(self) -> None:
        mode = self.detection_mode.currentText()
        topk_enabled = mode in ["Top-K Analysis", "Advanced Routing"]
        self.topk_k.setEnabled(topk_enabled)
        advanced_enabled = mode == "Advanced Routing"
        self.routing_header.setEnabled(advanced_enabled)
        self.tau_low.setEnabled(advanced_enabled)
        self.delta_close.setEnabled(advanced_enabled)
        self.enable_translation_test.setEnabled(advanced_enabled)
        test_enabled = advanced_enabled and self.enable_translation_test.isChecked()
        self.tau_en.setEnabled(test_enabled)
        self.similarity_threshold.setEnabled(test_enabled)
        self.probe_slice.setEnabled(test_enabled)

    # --- File selection ---------------------------------------------------
    def _browse_file(self) -> None:
        path, _flt = QFileDialog.getOpenFileName(
            self,
            "Select a document",
            "",
            "Documents (*.md *.markdown *.txt *.docx *.pdf *.xlsx *.msg *.jpg *.jpeg);;All files (*)",
        )
        if path:
            self.file_edit.setText(path)

    # --- Detection actions ------------------------------------------------
    def _on_cancel(self) -> None:
        if self._current and len(self._current) == 2:
            _t, w = self._current
            try:
                w.cancel()
                self._append_log("User cancelled. Waiting for safe stop…")
            except Exception:
                pass
        self.cancel_btn.setEnabled(False)

    def _on_detect(self) -> None:
        path = self.file_edit.text().strip()
        if not path:
            self.output.setPlainText("Please select a document to analyze.")
            return
        cand_raw = self.candidates_edit.text().strip()
        candidates = (
            [s.strip().lower() for s in cand_raw.split(",") if s.strip()] if cand_raw else None
        )
        max_chars = int(self.max_chars_sp.value())
        min_chars = int(self.min_chars_sp.value())
        mode = self.detection_mode.currentText()

        self.output.setPlainText("")
        self._set_busy(True)

        def job(progress=None, should_cancel=None):
            if mode == "Basic Detection":
                return self.svc.lang_detect_file(
                    path,
                    candidates=candidates,
                    max_chars=max_chars,
                    min_chars=min_chars,
                    progress=progress,
                    should_cancel=should_cancel,
                )
            elif mode == "Top-K Analysis":
                return self.svc.lang_detect_topk(
                    path,
                    k=int(self.topk_k.value()),
                    candidates=candidates,
                    max_chars=max_chars,
                    min_chars=min_chars,
                    progress=progress,
                    should_cancel=should_cancel,
                )
            else:
                return self.svc.lang_detect_advanced_routing(
                    path,
                    k=int(self.topk_k.value()),
                    candidates=candidates,
                    max_chars=max_chars,
                    min_chars=min_chars,
                    routing_config={
                        "tau_low": self.tau_low.value() / 100.0,
                        "delta_close": self.delta_close.value() / 100.0,
                        "tau_en": self.tau_en.value() / 100.0,
                        "similarity_noop_threshold": self.similarity_threshold.value() / 100.0,
                    },
                    enable_translation_test=self.enable_translation_test.isChecked(),
                    progress=progress,
                    should_cancel=should_cancel,
                )

        def on_result(res: dict[str, Any]):
            try:
                if res.get("cancelled"):
                    prev = self.output.toPlainText()
                    self.output.setPlainText(prev + ("\n" if prev else "") + "Cancelled.")
                    return
                if mode == "Basic Detection":
                    self._display_basic_result(res)
                elif mode == "Top-K Analysis":
                    self._display_topk_result(res)
                else:
                    self._display_advanced_result(res)
            finally:
                self._set_busy(False)
                self.cancel_btn.setEnabled(False)
                self._current = None

        def on_error(err: str):
            try:
                self.output.setPlainText(f"Detection failed: {err}")
            finally:
                self._set_busy(False)
                self.cancel_btn.setEnabled(False)
                self._current = None

        t, w = start_worker(job, on_log=self._append_log, on_result=on_result, on_error=on_error)
        self._jobs.append((t, w))
        self._current = (t, w)

    # --- Result formatting ------------------------------------------------
    def _display_basic_result(self, res: dict[str, Any]) -> None:
        if res.get("error"):
            self.output.setPlainText(res["error"])
            return
        lines = [
            "BASIC DETECTION:",
            f"  file={res.get('path')}",
            f"  lang={res.get('lang')} confidence={res.get('confidence'):.2f}",
            f"  engine={res.get('engine')} chars_used={res.get('chars_used')}",
            f"  log={res.get('log')}",
        ]
        self.output.setPlainText("\n".join(lines))

    def _display_topk_result(self, res: dict[str, Any]) -> None:
        if res.get("error"):
            self.output.setPlainText(res["error"])
            return
        topk = res.get("topk") or []
        lines = [
            "TOP-K DETECTION:",
            f"  file={res.get('path')}",
            f"  primary={res.get('lang_code')} conf={res.get('confidence'):.2f}",
            f"  chars_used={res.get('chars_used')} log={res.get('log')}",
            "  candidates:",
        ]
        for c in topk:
            lines.append(
                f"    - {c.get('lang')} score={c.get('score'):.4f} conf={c.get('confidence'):.4f} flags={c.get('flags')}"
            )
        self.output.setPlainText("\n".join(lines))

    def _display_advanced_result(self, res: dict[str, Any]) -> None:
        if res.get("error"):
            self.output.setPlainText(res["error"])
            return
        topk = res.get("topk") or []
        routing = res.get("routing") or {}
        lines = [
            "ADVANCED ROUTING ANALYSIS:",
            f"  file={res.get('path')}",
            f"  primary={res.get('lang_code')} conf={res.get('confidence'):.2f} chars_used={res.get('chars_used')}",
            f"  routing probe_triggered={routing.get('probe_triggered')} selected_src={routing.get('selected_src')} reason={routing.get('probe_reason')}",
        ]
        trtest = routing.get("translation_test")
        if isinstance(trtest, dict):
            lines.append(
                f"  translation_test en_conf={trtest.get('en_confidence'):.2f} sim={trtest.get('similarity'):.2f} passed={trtest.get('passed')}"
            )
        lines.append("  candidates:")
        for c in topk:
            lines.append(
                f"    - {c.get('lang')} score={c.get('score'):.4f} conf={c.get('confidence'):.4f} flags={c.get('flags')}"
            )
        lines.append(f"  log={res.get('log')}")
        self.output.setPlainText("\n".join(lines))
