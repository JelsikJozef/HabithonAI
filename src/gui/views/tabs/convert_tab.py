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
from ..ui_helpers import section_header, auto_expand_combo, wrap_with_help  # updated import

from ...services.facade import GuiServices
from ...services.async_worker import start_worker  # NEW


class ConvertTab(QWidget):
    """Preprocessing tab: plan and run convert-only workflow."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.svc = GuiServices()
        self._jobs: list[tuple] = []  # keep thread/worker refs
        self._current: tuple | None = None  # (thread, worker)

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
        form.addRow(wrap_with_help(self.recurse_cb, "Scan subdirectories recursively."))
        form.addRow(
            wrap_with_help(self.overwrite_cb, "Overwrite existing outputs instead of skipping.")
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
        form.addRow("Workers:", self.workers_sp)
        form.addRow("Progress:", self.progress_mode)
        form.addRow("Log level:", self.log_level)
        # expand combos
        for _c in (self.progress_mode, self.log_level):
            auto_expand_combo(_c)

        vbox.addLayout(form)

        # What to run (single Run button will honor these)
        run_opts = QFormLayout()
        self.convert_cb = QCheckBox("Convert to Markdown")
        self.convert_cb.setChecked(True)
        self.convert_cb.setToolTip(
            "Perform document-to-Markdown conversion into the Output folder."
        )
        self.translate_cb = QCheckBox("Translate to English")
        run_opts.addRow(
            wrap_with_help(self.convert_cb, "Convert supported documents to Markdown (.md).")
        )
        run_opts.addRow(
            wrap_with_help(
                self.translate_cb, "Translate Markdown to English using selected engine."
            )
        )
        vbox.addLayout(run_opts)

        # Translation options (enabled only when Translate is selected)
        tr_form = QFormLayout()
        self.make_en_cb = QCheckBox("Make English variant (_en.md)")
        self.make_en_cb.setChecked(False)
        self.make_en_cb.setToolTip(
            "If checked, creates/updates English-sidecar files with the _en.md suffix. If unchecked, updates inline language where applicable."
        )
        self.translator_combo = QComboBox()
        self.translator_combo.addItems(["auto", "marian_opus", "ct2_nllb"])  # auto -> None
        self.translator_combo.setToolTip(
            "Choose translation engine (auto selects the best available)."
        )
        tr_form.addRow(self.make_en_cb)
        tr_form.addRow("Translator:", self.translator_combo)
        auto_expand_combo(self.translator_combo)

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

        # Advanced routing controls (new)
        routing_group = QFormLayout()
        routing_label = section_header("Advanced Routing (Model-Only)")
        tr_form.addRow(routing_label)

        self.enable_routing_cb = QCheckBox("Enable advanced routing")
        self.enable_routing_cb.setChecked(True)
        self.enable_routing_cb.setToolTip(
            "Use model-only routing with probe selection, validation, and retry logic"
        )
        tr_form.addRow(
            wrap_with_help(
                self.enable_routing_cb,
                "Enable dynamic probe & retry logic for translation routing.",
            )
        )

        # Threshold controls
        self.tau_low = QSpinBox()
        self.tau_low.setRange(1, 100)
        self.tau_low.setValue(70)
        self.tau_low.setSuffix("%")
        self.tau_low.setToolTip(
            "Low confidence threshold - triggers probe when LangID confidence below this"
        )

        self.delta_close = QSpinBox()
        self.delta_close.setRange(1, 20)
        self.delta_close.setValue(5)
        self.delta_close.setSuffix("%")
        self.delta_close.setToolTip(
            "Close margin threshold - triggers probe when top-2 languages within this margin"
        )

        self.tau_en = QSpinBox()
        self.tau_en.setRange(1, 100)
        self.tau_en.setValue(90)
        self.tau_en.setSuffix("%")
        self.tau_en.setToolTip(
            "English confidence threshold - translation output must meet this to pass validation"
        )

        self.similarity_noop = QSpinBox()
        self.similarity_noop.setRange(1, 100)
        self.similarity_noop.setValue(92)
        self.similarity_noop.setSuffix("%")
        self.similarity_noop.setToolTip(
            "Near-identity threshold - reject translations too similar to input"
        )

        # Probe controls
        self.probe_k = QSpinBox()
        self.probe_k.setRange(1, 10)
        self.probe_k.setValue(3)
        self.probe_k.setToolTip("Number of candidate languages to test in probe phase")

        self.probe_slice = QSpinBox()
        self.probe_slice.setRange(100, 2000)
        self.probe_slice.setValue(600)
        self.probe_slice.setToolTip("Character count for micro-translation probe samples")

        self.max_retries = QSpinBox()
        self.max_retries.setRange(1, 10)
        self.max_retries.setValue(3)
        self.max_retries.setToolTip("Maximum retry attempts across engines and source languages")

        tr_form.addRow(self.enable_routing_cb)
        tr_form.addRow("Low confidence (τ_low):", self.tau_low)
        tr_form.addRow("Close margin (δ_close):", self.delta_close)
        tr_form.addRow("English threshold (τ_en):", self.tau_en)
        tr_form.addRow("Similarity threshold:", self.similarity_noop)
        tr_form.addRow("Probe candidates:", self.probe_k)
        tr_form.addRow("Probe slice chars:", self.probe_slice)
        tr_form.addRow("Max retries:", self.max_retries)

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
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setToolTip("Request cancellation of the current run.")
        self.cancel_btn.setEnabled(False)
        actions.addWidget(
            wrap_with_help(self.plan_btn, "Dry-run: show counts of convertible files.")
        )
        actions.addWidget(
            wrap_with_help(self.run_btn, "Execute selected stages: Convert and/or Translate.")
        )
        actions.addWidget(self.cancel_btn)
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
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.translate_cb.toggled.connect(self._update_enabled_states)
        self.convert_cb.toggled.connect(self._update_enabled_states)
        self.enable_routing_cb.toggled.connect(self._update_enabled_states)

        # Initialize enabled/disabled state
        self._update_enabled_states()

    # --- Helpers ---

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
        # Signal cancel to all and wait for threads to finish
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

    def _on_cancel(self) -> None:
        if self._current and len(self._current) == 2:
            _t, w = self._current
            try:
                w.cancel()
                self._append_log("User cancelled. Waiting for safe stop…")
            except Exception:
                pass
        self.cancel_btn.setEnabled(False)

    def _set_busy(self, busy: bool) -> None:
        for w in [
            self.plan_btn,
            self.run_btn,
            self.src_edit,
            self.out_edit,
            self.recurse_cb,
            self.overwrite_cb,
            self.workers_sp,
            self.progress_mode,
            self.log_level,
            self.convert_cb,
            self.translate_cb,
            self.make_en_cb,
            self.translator_combo,
            self.lang_cands,
            self.lang_max_chars,
            self.lang_min_chars,
            self.enable_routing_cb,
            self.tau_low,
            self.delta_close,
            self.tau_en,
            self.similarity_noop,
            self.probe_k,
            self.probe_slice,
            self.max_retries,
        ]:
            try:
                w.setEnabled(not busy)
            except Exception:
                pass
        # Cancel button is only enabled while busy
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
        # Translation options enabled only if translate is selected
        tr_enabled = self.translate_cb.isChecked()
        self.make_en_cb.setEnabled(tr_enabled)
        self.translator_combo.setEnabled(tr_enabled)
        self.lang_cands.setEnabled(tr_enabled)
        self.lang_max_chars.setEnabled(tr_enabled)
        self.lang_min_chars.setEnabled(tr_enabled)

        # Advanced routing controls enabled when translation is selected
        self.enable_routing_cb.setEnabled(tr_enabled)
        routing_enabled = tr_enabled and self.enable_routing_cb.isChecked()
        self.tau_low.setEnabled(routing_enabled)
        self.delta_close.setEnabled(routing_enabled)
        self.tau_en.setEnabled(routing_enabled)
        self.similarity_noop.setEnabled(routing_enabled)
        self.probe_k.setEnabled(routing_enabled)
        self.probe_slice.setEnabled(routing_enabled)
        self.max_retries.setEnabled(routing_enabled)

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
            # Attach routing only when enabled
            if self.enable_routing_cb.isChecked():
                cfg["routing"] = {
                    "tau_low": self.tau_low.value() / 100.0,
                    "delta_close": self.delta_close.value() / 100.0,
                    "tau_en": self.tau_en.value() / 100.0,
                    "similarity_noop_threshold": self.similarity_noop.value() / 100.0,
                    "probe": {
                        "k": int(self.probe_k.value()),
                        "slice_chars": int(self.probe_slice.value()),
                    },
                    "max_retries": int(self.max_retries.value()),
                }
        return cfg

    def _on_plan(self) -> None:
        if not self.convert_cb.isChecked():
            self.output.setPlainText(
                "Plan works with Convert. Enable 'Convert to Markdown' to preview."
            )
            return
        cfg = dict(self._cfg())
        self.output.setPlainText("")
        self._set_busy(True)

        def job(progress=None, should_cancel=None):
            return self.svc.convert_plan(cfg, progress=progress, should_cancel=should_cancel)

        def on_result(plan: dict):
            try:
                if plan.get("cancelled"):
                    prev = self.output.toPlainText()
                    self.output.setPlainText((prev + ("\n" if prev else "") + "Cancelled."))
                    return
                summary = plan.get("summary", {})
                lines = [
                    "Plan computed:",
                    f"  matched={summary.get('matched')} would_convert={summary.get('would_convert')} would_skip_existing={summary.get('would_skip_existing')}",
                ]
                prev = self.output.toPlainText()
                nl = "\n" if prev else ""
                self.output.setPlainText(prev + nl + "\n".join(lines))
            finally:
                self._set_busy(False)
                self.cancel_btn.setEnabled(False)
                self._current = None

        def on_error(err: str):
            try:
                self.output.setPlainText(f"Plan failed: {err}")
            finally:
                self._set_busy(False)
                self.cancel_btn.setEnabled(False)
                self._current = None

        t, w = start_worker(job, on_log=self._append_log, on_result=on_result, on_error=on_error)
        self._jobs.append((t, w))
        self._current = (t, w)

    def _on_run_combined(self) -> None:
        try:
            cfg = dict(self._cfg())
            # Validate basic inputs
            if not cfg.get("src") or not cfg.get("out"):
                self.output.setPlainText("Please select Source and Output folders.")
                return
            if not (self.convert_cb.isChecked() or self.translate_cb.isChecked()):
                self.output.setPlainText("Nothing selected to run. Check Convert and/or Translate.")
                return
        except Exception as e:
            self.output.setPlainText(f"Invalid configuration: {e}")
            return

        self.output.setPlainText("")
        self._set_busy(True)

        def job(progress=None, should_cancel=None):
            results: dict[str, Any] = {}
            if self.convert_cb.isChecked():
                res = self.svc.convert_run(cfg, progress=progress, should_cancel=should_cancel)
                results["convert"] = res
                if isinstance(res, dict) and res.get("cancelled"):
                    return {"cancelled": True, **results}
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
                    progress=progress,
                    should_cancel=should_cancel,
                )
                results["translate"] = tres
            return results

        def on_result(results: dict[str, Any]):
            try:
                if results.get("cancelled"):
                    prev = self.output.toPlainText()
                    self.output.setPlainText(prev + ("\n" if prev else "") + "Cancelled.")
                    return
                lines: list[str] = []
                if "convert" in results:
                    res = results["convert"] or {}
                    if res.get("cancelled"):
                        lines.append("Convert: cancelled.")
                    else:
                        lines += [
                            "Convert finished:",
                            f"  matched={res.get('matched')} ok={res.get('converted_ok')} skip={res.get('skipped_existing')} failed={res.get('failed')}",
                            f"  started_at={res.get('started_at')} ended_at={res.get('ended_at')}",
                            "",
                        ]
                if "translate" in results:
                    tres = results["translate"] or {}
                    if tres.get("cancelled"):
                        lines.append("Translate: cancelled.")
                    else:
                        code = tres.get("code")
                        report = tres.get("report") or {}
                        tr = report.get("translation") if isinstance(report, dict) else None
                        lines.append(f"Translate exit code: {code}")
                        if isinstance(tr, dict):
                            created = tr.get("created")
                            skipped = tr.get("skipped_exists")
                            failed = tr.get("failed")
                            lines.append(f"  created={created} skipped={skipped} failed={failed}")
                        rp = tres.get("report_path")
                        if rp:
                            lines.append(f"  report={rp}")
                prev = self.output.toPlainText()
                nl = "\n" if prev else ""
                self.output.setPlainText(prev + nl + "\n".join(lines) if lines else prev)
            finally:
                self._set_busy(False)
                self.cancel_btn.setEnabled(False)
                self._current = None

        def on_error(err: str):
            try:
                self.output.setPlainText(f"Run failed: {err}")
            finally:
                self._set_busy(False)
                self.cancel_btn.setEnabled(False)
                self._current = None

        t, w = start_worker(job, on_log=self._append_log, on_result=on_result, on_error=on_error)
        self._jobs.append((t, w))
        self._current = (t, w)
