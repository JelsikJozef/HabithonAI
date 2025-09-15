from __future__ import annotations

from typing import Any, Mapping

from ..qt import (
    QWidget,
    QVBoxLayout,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QCheckBox,
    QTextEdit,
    QSpinBox,
    QComboBox,
    QLabel,
    QToolButton,
)

from ...services.facade import GuiServices


class ConvertTab(QWidget):
    """Preprocessing tab: plan and run convert-only workflow with advanced routing."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.svc = GuiServices()

        vbox = QVBoxLayout(self)
        form = QFormLayout()

        # Paths
        self.src_edit = QLineEdit()
        self.out_edit = QLineEdit()
        pick_src = QPushButton("Browse…")
        pick_out = QPushButton("Browse…")
        row_src = QHBoxLayout()
        row_src.addWidget(self.src_edit, 1)
        row_src.addWidget(pick_src)
        row_out = QHBoxLayout()
        row_out.addWidget(self.out_edit, 1)
        row_out.addWidget(pick_out)
        form.addRow("Source folder:", row_src)
        form.addRow("Output folder:", row_out)

        # Options
        self.recurse_cb = QCheckBox("Recurse subfolders")
        self.recurse_cb.setChecked(True)
        self.overwrite_cb = QCheckBox("Overwrite existing")
        self.overwrite_cb.setChecked(False)
        self.workers_sp = QSpinBox()
        self.workers_sp.setRange(1, 64)
        self.workers_sp.setValue(1)
        self.progress_mode = QComboBox()
        self.progress_mode.addItems(["auto", "plain", "none"])
        self.log_level = QComboBox()
        self.log_level.addItems(["ERROR", "WARNING", "INFO", "DEBUG"])

        form.addRow(self.recurse_cb)
        form.addRow(self.overwrite_cb)
        form.addRow("Workers:", self.workers_sp)
        form.addRow("Progress:", self.progress_mode)
        form.addRow("Log level:", self.log_level)
        vbox.addLayout(form)

        # What to run
        run_opts = QFormLayout()
        self.convert_cb = QCheckBox("Convert to Markdown")
        self.convert_cb.setChecked(True)
        self.translate_cb = QCheckBox("Translate to English")
        self.translate_cb.setChecked(False)
        run_opts.addRow(self.convert_cb)
        run_opts.addRow(self.translate_cb)
        vbox.addLayout(run_opts)

        # Translation options
        tr_form = QFormLayout()

        # Basic translation controls
        self.make_en_cb = QCheckBox("Make English variant (.en.md)")
        self.make_en_cb.setChecked(False)
        tr_form.addRow(
            self._create_label_with_help(
                "English variant:",
                "If checked, creates/updates English-sidecar files. If unchecked, updates inline language where applicable.",
            ),
            self.make_en_cb,
        )

        self.translator_combo = QComboBox()
        self.translator_combo.addItems(["auto", "marian_opus", "ct2_nllb"])
        tr_form.addRow(
            self._create_label_with_help(
                "Translator:",
                "Choose translation engine. 'auto' selects the best available engine automatically.",
            ),
            self.translator_combo,
        )

        # Language Detection Settings
        lang_label = QLabel("Language Detection Settings")
        lang_label.setStyleSheet("font-weight: bold; color: #2c5aa0; margin-top: 10px;")
        tr_form.addRow(lang_label)

        self.lang_cands = QLineEdit()
        self.lang_cands.setPlaceholderText("e.g. sk,cs,de,en")
        tr_form.addRow(
            self._create_label_with_help(
                "Candidates:",
                "Optional comma-separated list of language codes to limit detection to. Leave empty to detect from all supported languages.",
            ),
            self.lang_cands,
        )

        self.lang_max_chars = QSpinBox()
        self.lang_max_chars.setRange(100, 50000)
        self.lang_max_chars.setValue(5000)
        tr_form.addRow(
            self._create_label_with_help(
                "Max chars:",
                "Maximum number of characters to analyze from each document. Larger values give more accurate results but take longer.",
            ),
            self.lang_max_chars,
        )

        self.lang_min_chars = QSpinBox()
        self.lang_min_chars.setRange(10, 1000)
        self.lang_min_chars.setValue(50)
        tr_form.addRow(
            self._create_label_with_help(
                "Min chars:",
                "Minimum number of characters required before trusting detection results. Very short texts are unreliable.",
            ),
            self.lang_min_chars,
        )

        # Advanced Routing Controls
        routing_label = QLabel("Advanced Routing (Model-Only)")
        routing_label.setStyleSheet("font-weight: bold; color: #2c5aa0; margin-top: 10px;")
        tr_form.addRow(routing_label)

        self.enable_routing_cb = QCheckBox("Enable advanced routing")
        self.enable_routing_cb.setChecked(True)
        tr_form.addRow(
            self._create_label_with_help(
                "Advanced routing:",
                "Use model-only routing with intelligent probe selection, quality validation, and deterministic retry logic for better translation accuracy.",
            ),
            self.enable_routing_cb,
        )

        # Routing threshold controls
        self.tau_low = QSpinBox()
        self.tau_low.setRange(1, 100)
        self.tau_low.setValue(70)
        self.tau_low.setSuffix("%")
        tr_form.addRow(
            self._create_label_with_help(
                "Low confidence (τ_low):",
                "Confidence threshold below which the system considers language detection unreliable and triggers probe analysis to find the best source language.",
            ),
            self.tau_low,
        )

        self.delta_close = QSpinBox()
        self.delta_close.setRange(1, 20)
        self.delta_close.setValue(5)
        self.delta_close.setSuffix("%")
        tr_form.addRow(
            self._create_label_with_help(
                "Close margin (δ_close):",
                "When the top two language candidates have scores within this margin, the detection is considered ambiguous and triggers probe analysis.",
            ),
            self.delta_close,
        )

        self.tau_en = QSpinBox()
        self.tau_en.setRange(1, 100)
        self.tau_en.setValue(90)
        self.tau_en.setSuffix("%")
        tr_form.addRow(
            self._create_label_with_help(
                "English threshold (τ_en):",
                "Minimum English confidence required for translation outputs to pass quality validation. Outputs below this threshold trigger retries.",
            ),
            self.tau_en,
        )

        self.similarity_noop = QSpinBox()
        self.similarity_noop.setRange(1, 100)
        self.similarity_noop.setValue(92)
        self.similarity_noop.setSuffix("%")
        tr_form.addRow(
            self._create_label_with_help(
                "Near-identity threshold:",
                "Maximum similarity allowed between source and translation. Higher similarity suggests poor translation or identity copying.",
            ),
            self.similarity_noop,
        )

        # Probe and retry controls
        self.probe_k = QSpinBox()
        self.probe_k.setRange(1, 10)
        self.probe_k.setValue(3)
        tr_form.addRow(
            self._create_label_with_help(
                "Probe candidates:",
                "Number of top language candidates to test with micro-translation probes when detection is ambiguous.",
            ),
            self.probe_k,
        )

        self.probe_slice = QSpinBox()
        self.probe_slice.setRange(100, 2000)
        self.probe_slice.setValue(600)
        tr_form.addRow(
            self._create_label_with_help(
                "Probe slice chars:",
                "Number of characters to use for micro-translation quality tests during probe analysis.",
            ),
            self.probe_slice,
        )

        self.max_retries = QSpinBox()
        self.max_retries.setRange(1, 10)
        self.max_retries.setValue(3)
        tr_form.addRow(
            self._create_label_with_help(
                "Max retries:",
                "Maximum number of retry attempts across different engines and source languages before marking a document as failed.",
            ),
            self.max_retries,
        )

        vbox.addLayout(tr_form)

        # Actions
        actions = QHBoxLayout()
        self.plan_btn = QPushButton("Plan")
        self.run_btn = QPushButton("Run")
        actions.addWidget(self.plan_btn)
        actions.addWidget(self.run_btn)
        actions.addStretch(1)
        vbox.addLayout(actions)

        # Output
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        vbox.addWidget(self.output, 1)

        # Signals
        pick_src.clicked.connect(self._browse_src)
        pick_out.clicked.connect(self._browse_out)
        self.plan_btn.clicked.connect(self._on_plan)
        self.run_btn.clicked.connect(self._on_run_combined)
        self.translate_cb.toggled.connect(self._update_enabled_states)
        self.convert_cb.toggled.connect(self._update_enabled_states)
        self.enable_routing_cb.toggled.connect(self._update_enabled_states)

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
        tr_enabled = self.translate_cb.isChecked()
        self.make_en_cb.setEnabled(tr_enabled)
        self.translator_combo.setEnabled(tr_enabled)
        self.lang_cands.setEnabled(tr_enabled)
        self.lang_max_chars.setEnabled(tr_enabled)
        self.lang_min_chars.setEnabled(tr_enabled)

        self.enable_routing_cb.setEnabled(tr_enabled)
        routing_enabled = tr_enabled and self.enable_routing_cb.isChecked()
        self.tau_low.setEnabled(routing_enabled)
        self.delta_close.setEnabled(routing_enabled)
        self.tau_en.setEnabled(routing_enabled)
        self.similarity_noop.setEnabled(routing_enabled)
        self.probe_k.setEnabled(routing_enabled)
        self.probe_slice.setEnabled(routing_enabled)
        self.max_retries.setEnabled(routing_enabled)

        self.plan_btn.setEnabled(self.convert_cb.isChecked())

    def _browse_src(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select source folder")
        if d:
            self.src_edit.setText(d)

    def _browse_out(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select output folder")
        if d:
            self.out_edit.setText(d)

    def _cfg(self) -> Mapping[str, Any]:
        cands_raw = self.lang_cands.text().strip()
        cands = [s.strip().lower() for s in cands_raw.split(",") if s.strip()] if cands_raw else []
        langid_cfg: dict[str, Any] = {}
        if cands:
            langid_cfg["candidates"] = cands
        mx = int(self.lang_max_chars.value())
        mn = int(self.lang_min_chars.value())
        if mx:
            langid_cfg["max_chars"] = mx
        if mn:
            langid_cfg["min_chars"] = mn

        # Build routing configuration when enabled
        routing_cfg: dict[str, Any] = {}
        if self.translate_cb.isChecked() and self.enable_routing_cb.isChecked():
            routing_cfg = {
                "tau_low": float(self.tau_low.value()) / 100.0,
                "delta_close": float(self.delta_close.value()) / 100.0,
                "tau_en": float(self.tau_en.value()) / 100.0,
                "similarity_noop_threshold": float(self.similarity_noop.value()) / 100.0,
                "probe": {
                    "k": int(self.probe_k.value()),
                    "slice_chars": int(self.probe_slice.value()),
                },
                "max_retries": int(self.max_retries.value()),
            }

        cfg: dict[str, Any] = {
            "src": self.src_edit.text().strip(),
            "out": self.out_edit.text().strip(),
            "scan": {"recurse": self.recurse_cb.isChecked()},
            "write": {
                "overwrite": self.overwrite_cb.isChecked(),
                "assets_subdir": "assets",
                "write_meta": "sidecar",
                "normalize_eol": "lf",
            },
            "runtime": {
                "workers": int(self.workers_sp.value()),
                "on_error": "skip",
                "dry_run": False,
                "strict": False,
            },
            "ui": {
                "log_level": self.log_level.currentText(),
                "progress": self.progress_mode.currentText(),
                "locale": None,
            },
            "report": None,
        }
        if self.translate_cb.isChecked():
            cfg["langid"] = langid_cfg
            if routing_cfg:
                cfg["routing"] = routing_cfg
        return cfg

    def _on_plan(self) -> None:
        if not self.convert_cb.isChecked():
            self.output.setPlainText(
                "Plan works with Convert. Enable 'Convert to Markdown' to preview."
            )
            return
        try:
            cfg = dict(self._cfg())
            plan = self.svc.convert_plan(cfg)
            summary = plan.get("summary", {})
            lines = [
                "Plan computed:",
                f"  matched={summary.get('matched')} would_convert={summary.get('would_convert')} would_skip_existing={summary.get('would_skip_existing')}",
            ]
            self.output.setPlainText("\n".join(lines))
        except Exception as e:
            self.output.setPlainText(f"Plan failed: {e}")

    def _on_run_combined(self) -> None:
        try:
            cfg = dict(self._cfg())
            if not cfg.get("src") or not cfg.get("out"):
                self.output.setPlainText("Please select Source and Output folders.")
                return
            if not (self.convert_cb.isChecked() or self.translate_cb.isChecked()):
                self.output.setPlainText("Nothing selected to run. Check Convert and/or Translate.")
                return

            lines: list[str] = []

            if self.convert_cb.isChecked():
                res = self.svc.convert_run(cfg)
                lines += [
                    "Convert finished:",
                    f"  matched={res.get('matched')} ok={res.get('converted_ok')} skip={res.get('skipped_existing')} failed={res.get('failed')}",
                    "",
                ]

            if self.translate_cb.isChecked():
                engine = self.translator_combo.currentText().strip()
                engine_opt = None if engine == "auto" else engine
                translate_only = not self.convert_cb.isChecked()
                make_en = self.make_en_cb.isChecked()
                tres = self.svc.translate_run(
                    cfg,
                    make_english=make_en,
                    translate_only=translate_only,
                    translator=engine_opt,
                )
                code = tres.get("code")
                report = tres.get("report") or {}
                tr = report.get("translation") if isinstance(report, dict) else None
                lines.append(f"Translate exit code: {code}")
                if isinstance(tr, dict):
                    created = tr.get("created")
                    skipped = tr.get("skipped_exists")
                    failed = tr.get("failed")
                    lines.append(f"  created={created} skipped={skipped} failed={failed}")
                    if tr.get("error"):
                        lines.append(f"  error={tr.get('error')}")
                rp = tres.get("report_path")
                if rp:
                    lines.append(f"  report={rp}")

            self.output.setPlainText("\n".join(lines))
        except Exception as e:
            self.output.setPlainText(f"Run failed: {e}")
