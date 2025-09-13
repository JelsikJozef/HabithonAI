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
        self.run_btn.clicked.connect(self._on_run)

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
