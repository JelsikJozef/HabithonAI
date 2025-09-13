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

        # Actions (convert-only)
        actions = QHBoxLayout()
        self.plan_btn = QPushButton("Plan")
        self.plan_help_btn = QPushButton("?")
        self.run_btn = QPushButton("Run")
        self.run_help_btn = QPushButton("?")
        actions.addWidget(self.plan_btn)
        actions.addWidget(self.plan_help_btn)
        actions.addWidget(self.run_btn)
        actions.addWidget(self.run_help_btn)
        actions.addStretch(1)
        vbox.addLayout(actions)

        # Translation controls (standalone)
        tr_form = QFormLayout()
        self.make_en_cb = QCheckBox("Make English variant")
        self.make_en_cb.setChecked(False)
        self.translate_only_cb = QCheckBox("Translate-only (skip convert phase)")
        self.translate_only_cb.setChecked(False)
        self.translator_combo = QComboBox()
        self.translator_combo.addItems(["auto", "marian_opus", "ct2_nllb"])  # auto -> None
        tr_form.addRow(self.make_en_cb)
        tr_form.addRow(self.translate_only_cb)
        tr_form.addRow("Translator:", self.translator_combo)
        vbox.addLayout(tr_form)

        tr_actions = QHBoxLayout()
        self.translate_btn = QPushButton("Translate")
        self.translate_help_btn = QPushButton("?")
        tr_actions.addWidget(self.translate_btn)
        tr_actions.addWidget(self.translate_help_btn)
        tr_actions.addStretch(1)
        vbox.addLayout(tr_actions)

        # Output
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        vbox.addWidget(self.output, 1)

        # Signals
        pick_src.clicked.connect(self._browse_src)
        pick_out.clicked.connect(self._browse_out)
        self.plan_btn.clicked.connect(self._on_plan)
        self.run_btn.clicked.connect(self._on_run)
        self.plan_help_btn.clicked.connect(self._on_help_plan)
        self.run_help_btn.clicked.connect(self._on_help_run)
        self.translate_btn.clicked.connect(self._on_translate)
        self.translate_help_btn.clicked.connect(self._on_help_translate)

    # --- Helpers ---

    def _browse_src(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select source folder")
        if d:
            self.src_edit.setText(d)

    def _browse_out(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select output folder")
        if d:
            self.out_edit.setText(d)

    def _cfg(self) -> Mapping[str, Any]:
        return {
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

    def _on_plan(self) -> None:
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

    def _on_run(self) -> None:
        try:
            cfg = dict(self._cfg())
            res = self.svc.convert_run(cfg)
            lines = [
                "Run finished:",
                f"  matched={res.get('matched')} ok={res.get('converted_ok')} skip={res.get('skipped_existing')} failed={res.get('failed')}",
                f"  started_at={res.get('started_at')} ended_at={res.get('ended_at')}",
            ]
            self.output.setPlainText("\n".join(lines))
        except Exception as e:
            self.output.setPlainText(f"Run failed: {e}")

    def _on_translate(self) -> None:
        try:
            cfg = dict(self._cfg())
            make_en = self.make_en_cb.isChecked()
            tr_only = self.translate_only_cb.isChecked()
            engine = self.translator_combo.currentText().strip()
            engine_opt = None if engine == "auto" else engine
            res = self.svc.translate_run(
                cfg,
                make_english=make_en,
                translate_only=tr_only,
                translator=engine_opt,
            )
            code = res.get("code")
            report = res.get("report") or {}
            # Summarize translation section when present
            tr = report.get("translation") if isinstance(report, dict) else None
            lines = [
                f"Translate exit code: {code}",
            ]
            if isinstance(tr, dict):
                # include a few key fields if available
                created = tr.get("created")
                skipped = tr.get("skipped_exists")
                failed = tr.get("failed")
                lines.append(f"  created={created} skipped={skipped} failed={failed}")
                if tr.get("error"):
                    lines.append(f"  error={tr.get('error')}")
            rp = res.get("report_path")
            if rp:
                lines.append(f"  report={rp}")
            self.output.setPlainText("\n".join(lines))
        except Exception as e:
            self.output.setPlainText(f"Translate failed: {e}")

    # --- Inline help handlers ---

    def _on_help_plan(self) -> None:
        help_text = (
            "Plan: scan the source folder and compute a deterministic, side-effect-free "
            "list of files that would be converted to Markdown. No files are written. "
            "Use this to verify what would be processed before running."
        )
        self.output.setPlainText(help_text)

    def _on_help_run(self) -> None:
        help_text = (
            "Run: execute the conversion of matched files to Markdown and write outputs "
            "to the chosen output folder. This performs parsing, normalization, and "
            "optional writing of metadata."
        )
        self.output.setPlainText(help_text)

    def _on_help_translate(self) -> None:
        help_text = (
            "Translate: create or refresh English Markdown variants. Use 'Translate-only' "
            "to skip convert and translate existing Markdown under Source; otherwise it will "
            "translate outputs from the last convert run. 'Translator' selects the engine "
            "(auto uses defaults)."
        )
        self.output.setPlainText(help_text)
