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
)

from ...services.facade import GuiServices


class ConvertTab(QWidget):
    """Preprocessing tab: plan and run convert-only workflow."""

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

        # Options (minimal subset)
        self.recurse_cb = QCheckBox("Recurse subfolders")
        self.recurse_cb.setChecked(True)
        self.recurse_cb.setToolTip("If checked, scan source folder recursively.")
        self.overwrite_cb = QCheckBox("Overwrite existing")
        self.overwrite_cb.setChecked(False)
        self.overwrite_cb.setToolTip(
            "If checked, existing outputs will be overwritten. If unchecked, they are skipped."
        )

        self.workers_sp = QSpinBox()
        self.workers_sp.setRange(1, 64)
        self.workers_sp.setValue(1)
        self.workers_sp.setToolTip("Maximum parallel workers for Convert/Translate.")
        self.progress_mode = QComboBox()
        self.progress_mode.addItems(["auto", "plain", "none"])
        self.progress_mode.setToolTip("Progress display style in logs.")
        self.log_level = QComboBox()
        self.log_level.addItems(["ERROR", "WARNING", "INFO", "DEBUG"])
        self.log_level.setToolTip("Verbosity of logs written during operations.")
        form.addRow(self.recurse_cb)
        form.addRow(self.overwrite_cb)
        form.addRow("Workers:", self.workers_sp)
        form.addRow("Progress:", self.progress_mode)
        form.addRow("Log level:", self.log_level)

        vbox.addLayout(form)

        # What to run (single Run button will honor these)
        run_opts = QFormLayout()
        self.convert_cb = QCheckBox("Convert to Markdown")
        self.convert_cb.setChecked(True)
        self.convert_cb.setToolTip(
            "Perform document-to-Markdown conversion into the Output folder."
        )
        self.translate_cb = QCheckBox("Translate to English")
        self.translate_cb.setChecked(False)
        self.translate_cb.setToolTip("Create or refresh English variants of Markdown files.")
        run_opts.addRow(self.convert_cb)
        run_opts.addRow(self.translate_cb)
        vbox.addLayout(run_opts)

        # Translation options (enabled only when Translate is selected)
        tr_form = QFormLayout()
        self.make_en_cb = QCheckBox("Make English variant (.en.md)")
        self.make_en_cb.setChecked(False)
        self.make_en_cb.setToolTip(
            "If checked, creates/updates English-sidecar files. If unchecked, updates inline language where applicable."
        )
        self.translator_combo = QComboBox()
        self.translator_combo.addItems(["auto", "marian_opus", "ct2_nllb"])  # auto -> None
        self.translator_combo.setToolTip(
            "Choose translation engine (auto selects the best available)."
        )
        tr_form.addRow(self.make_en_cb)
        tr_form.addRow("Translator:", self.translator_combo)

        # LangID controls for translation routing
        self.lang_cands = QLineEdit()
        self.lang_cands.setPlaceholderText("e.g. sk,cs,de,en")
        self.lang_cands.setToolTip("Optional comma-separated detector candidates to bias routing.")
        self.lang_max_chars = QSpinBox()
        self.lang_max_chars.setRange(100, 50000)
        self.lang_max_chars.setValue(5000)
        self.lang_max_chars.setToolTip("Max cleaned characters analyzed for language detection.")
        self.lang_min_chars = QSpinBox()
        self.lang_min_chars.setRange(10, 1000)
        self.lang_min_chars.setValue(50)
        self.lang_min_chars.setToolTip("Minimum characters before trusting detector scores.")
        tr_form.addRow(QLabel("LangID candidates:"), self.lang_cands)
        tr_form.addRow(QLabel("LangID max chars:"), self.lang_max_chars)
        tr_form.addRow(QLabel("LangID min chars:"), self.lang_min_chars)

        vbox.addLayout(tr_form)

        # Actions (single Run + optional Plan)
        actions = QHBoxLayout()
        self.plan_btn = QPushButton("Plan")
        self.plan_btn.setToolTip(
            "Dry-run for Convert: list what would be converted without writing files."
        )
        self.run_btn = QPushButton("Run")
        self.run_btn.setToolTip(
            "Run selected actions in order: Convert (if checked) then Translate (if checked)."
        )
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

        # Initialize enabled/disabled state
        self._update_enabled_states()

    # --- Helpers ---

    def _update_enabled_states(self) -> None:
        # Translation options enabled only if translate is selected
        tr_enabled = self.translate_cb.isChecked()
        self.make_en_cb.setEnabled(tr_enabled)
        self.translator_combo.setEnabled(tr_enabled)
        self.lang_cands.setEnabled(tr_enabled)
        self.lang_max_chars.setEnabled(tr_enabled)
        self.lang_min_chars.setEnabled(tr_enabled)
        # Plan is meaningful only when Convert is selected
        self.plan_btn.setEnabled(self.convert_cb.isChecked())
        # Run tooltip reflects current selection
        acts: list[str] = []
        if self.convert_cb.isChecked():
            acts.append("Convert")
        if self.translate_cb.isChecked():
            acts.append("Translate")
        what = ", then ".join(acts) if len(acts) == 2 else (acts[0] if acts else "nothing")
        self.run_btn.setToolTip(f"Run: {what}.")

    def _browse_src(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select source folder")
        if d:
            self.src_edit.setText(d)

    def _browse_out(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select output folder")
        if d:
            self.out_edit.setText(d)

    def _cfg(self) -> Mapping[str, Any]:
        # Build optional LangID section when any field is set
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

        cfg: dict[str, Any] = {
            "src": self.src_edit.text().strip(),
            "out": self.out_edit.text().strip(),
            "scan": {
                "recurse": self.recurse_cb.isChecked(),
                # Let app layer default include_ext and exclude_glob
            },
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
                # Optional: still pass locale as log hint
                "locale": None,
            },
            "report": None,
        }
        if self.translate_cb.isChecked():
            # Attach langid preferences only when translate is requested
            cfg["langid"] = langid_cfg
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
        actions_taken: list[str] = []
        try:
            cfg = dict(self._cfg())

            # Validate basic inputs
            if not cfg.get("src") or not cfg.get("out"):
                self.output.setPlainText("Please select Source and Output folders.")
                return
            if not (self.convert_cb.isChecked() or self.translate_cb.isChecked()):
                self.output.setPlainText("Nothing selected to run. Check Convert and/or Translate.")
                return

            lines: list[str] = []

            # 1) Convert
            if self.convert_cb.isChecked():
                res = self.svc.convert_run(cfg)
                actions_taken.append("convert")
                lines += [
                    "Convert finished:",
                    f"  matched={res.get('matched')} ok={res.get('converted_ok')} skip={res.get('skipped_existing')} failed={res.get('failed')}",
                    f"  started_at={res.get('started_at')} ended_at={res.get('ended_at')}",
                    "",
                ]

            # 2) Translate
            if self.translate_cb.isChecked():
                engine = self.translator_combo.currentText().strip()
                engine_opt = None if engine == "auto" else engine
                # translate_only is implied by whether Convert ran
                translate_only = not self.convert_cb.isChecked()
                make_en = self.make_en_cb.isChecked()
                tres = self.svc.translate_run(
                    cfg,
                    make_english=make_en,
                    translate_only=translate_only,
                    translator=engine_opt,
                )
                actions_taken.append("translate")
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

            if actions_taken:
                self.output.setPlainText("\n".join(lines))
            else:
                self.output.setPlainText("No actions executed.")
        except Exception as e:
            self.output.setPlainText(f"Run failed: {e}")
