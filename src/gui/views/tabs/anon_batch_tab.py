from __future__ import annotations

"""Batch anonymization tab: run deterministic anonymization or pseudonymization over a folder.

Outputs anonymized Markdown files next to (or parallel to) preprocessing outputs.

Design goals:
- Minimal required inputs (source, output)
- Mode toggle (Deterministic vs Pseudonymize)
- Optional language hint & tenant id
- Plan (dry summary) + Run (execute)
- Recurse + Overwrite flags
"""

from typing import Any, Mapping

from ..qt import (
    QWidget,
    QVBoxLayout,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QComboBox,
    QCheckBox,
    QTextEdit,
    QLabel,
)
from ..ui_helpers import create_info_icon  # new helper import
from ..ui_helpers import create_field_label, auto_expand_combo, wrap_with_help  # added

from ...services.facade import GuiServices
from ...services.async_worker import start_worker  # NEW


class AnonBatchTab(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.svc = GuiServices()
        self._jobs: list[tuple] = []  # keep thread/worker refs
        self._current: tuple | None = None  # (thread, worker)

        vbox = QVBoxLayout(self)
        form = QFormLayout()

        # Source / Output folder pickers
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

        # Mode + options
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["deterministic", "pseudonymize"])
        mode_help = (
            "<b>Deterministic</b>: Replace PII with stable HMAC hash tokens (h:kid:hex).\n"
            "Same input + tenant + keyset => same token across documents. Enables linking (e.g. same email -> same token). \n"
            "Token shape: <code>h:&lt;kid&gt;:&lt;hex&gt;</code>.\n"
            "<b>Pseudonymize</b>: Replace PII with one-off random structured placeholders like <code>{{PII:EMAIL:1:deadbeef}}</code> that are <i>not</i> stable across runs/files.\n"
            "Good for isolation when you do NOT want cross-document correlation.\n"
            "Both modes store token->original mappings in the vault; restoration needs the per-file context id. Tenant ID only impacts deterministic hashing."
        )
        self.mode_combo.setToolTip(mode_help)
        auto_expand_combo(self.mode_combo)
        mode_row = QHBoxLayout()
        mode_row.addWidget(self.mode_combo, 1)
        info_lbl = create_info_icon(mode_help)  # replaced custom label
        mode_row.addWidget(info_lbl, 0)
        form.addRow("Mode:", mode_row)

        self.lang_edit = QLineEdit()
        self.lang_edit.setPlaceholderText("Optional language hint (e.g. en, de, sk)")
        form.addRow(
            create_field_label(
                "Language hint:",
                "Optional ISO language hint to improve detector precision and reduce false positives.",
            ),
            self.lang_edit,
        )

        self.tenant_edit = QLineEdit()
        self.tenant_edit.setPlaceholderText("Tenant ID (deterministic mode only)")
        self.tenant_edit.setToolTip(
            "Optional namespace used when hashing in deterministic mode. Different tenant IDs produce different tokens for the same value. Ignored in pseudonymize mode."
        )
        form.addRow(
            create_field_label(
                "Tenant ID:",
                "Scopes deterministic hashing; different tenant -> different token for same PII.",
            ),
            self.tenant_edit,
        )

        self.recurse_cb = QCheckBox("Recurse subfolders")
        self.recurse_cb.setChecked(True)
        form.addRow(self.recurse_cb)

        self.overwrite_cb = QCheckBox("Overwrite existing outputs")
        self.overwrite_cb.setChecked(False)
        self.overwrite_cb.setToolTip(
            "If unchecked, existing *.anonymized.md / *.pseudonymized.md are skipped."
        )
        form.addRow(self.overwrite_cb)

        vbox.addLayout(form)

        # Actions
        actions = QHBoxLayout()
        self.plan_btn = QPushButton("Plan")
        self.run_btn = QPushButton("Run")
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setToolTip("Request cancellation of the current batch operation.")
        self.cancel_btn.setEnabled(False)
        self.plan_btn.setToolTip("Dry summary: counts of files that would be processed / skipped.")
        self.run_btn.setToolTip("Execute batch anonymization.")
        actions.addWidget(wrap_with_help(self.plan_btn, "Dry summary of files to process or skip."))
        actions.addWidget(
            wrap_with_help(self.run_btn, "Execute batch anonymization (writes output files).")
        )
        actions.addWidget(self.cancel_btn)
        actions.addStretch(1)
        vbox.addLayout(actions)

        # Output area
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("Plan or run to see results…")
        vbox.addWidget(self.output, 1)

        # Signals
        pick_src.clicked.connect(self._browse_src)
        pick_out.clicked.connect(self._browse_out)
        self.plan_btn.clicked.connect(self._on_plan)
        self.run_btn.clicked.connect(self._on_run)
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.mode_combo.currentTextChanged.connect(self._update_enabled)

        self._update_enabled()

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

    # --- Helpers ------------------------------------------------------------
    def _set_busy(self, busy: bool) -> None:
        for w in [
            self.plan_btn,
            self.run_btn,
            self.src_edit,
            self.out_edit,
            self.mode_combo,
            self.lang_edit,
            self.tenant_edit,
            self.recurse_cb,
            self.overwrite_cb,
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
            "mode": self.mode_combo.currentText().strip().lower(),
            "language": self.lang_edit.text().strip() or None,
            "tenant_id": self.tenant_edit.text().strip() or None,
            "recurse": self.recurse_cb.isChecked(),
            "overwrite": self.overwrite_cb.isChecked(),
        }

    def _update_enabled(self) -> None:
        deterministic = self.mode_combo.currentText().lower() == "deterministic"
        self.tenant_edit.setEnabled(deterministic)
        if not deterministic:
            self.tenant_edit.setToolTip(
                "Disabled: tenant applies only to deterministic hashing mode."
            )

    # --- Actions ------------------------------------------------------------
    def _on_cancel(self) -> None:
        if self._current and len(self._current) == 2:
            _t, w = self._current
            try:
                w.cancel()
                self._append_log("User cancelled. Waiting for safe stop…")
            except Exception:
                pass
        self.cancel_btn.setEnabled(False)

    def _on_plan(self) -> None:
        cfg = dict(self._cfg())
        if not cfg.get("src") or not cfg.get("out"):
            self.output.setPlainText("Select source and output folders first.")
            return
        self.output.setPlainText("")
        self._set_busy(True)

        def job(progress=None, should_cancel=None):
            return self.svc.anon_batch_plan(cfg, progress=progress, should_cancel=should_cancel)

        def on_result(res: dict):
            try:
                if res.get("cancelled"):
                    prev = self.output.toPlainText()
                    self.output.setPlainText(prev + ("\n" if prev else "") + "Cancelled.")
                    return
                if res.get("error"):
                    self.output.setPlainText(f"Plan error: {res['error']}")
                    return
                lines = [
                    "Batch Anonymization Plan:",
                    f"  mode={res.get('mode')} recurse={cfg.get('recurse')} overwrite={cfg.get('overwrite')}",
                    f"  matched={res.get('matched')} would_process={res.get('would_process')} would_skip_existing={res.get('would_skip_existing')}",
                    "  (Deterministic => stable hash tokens; Pseudonymize => random one-off tokens)",
                ]
                prev = self.output.toPlainText()
                nl = "\n" if prev else ""
                self.output.setPlainText(prev + nl + "\n".join(lines))
            finally:
                self._set_busy(False)
                self._current = None

        def on_error(err: str):
            try:
                self.output.setPlainText(f"Plan failed: {err}")
            finally:
                self._set_busy(False)
                self._current = None

        t, w = start_worker(job, on_log=self._append_log, on_result=on_result, on_error=on_error)
        self._jobs.append((t, w))
        self._current = (t, w)

    def _on_run(self) -> None:
        cfg = dict(self._cfg())
        if not cfg.get("src") or not cfg.get("out"):
            self.output.setPlainText("Select source and output folders first.")
            return
        self.output.setPlainText("")
        self._set_busy(True)

        def job(progress=None, should_cancel=None):
            return self.svc.anon_batch_run(cfg, progress=progress, should_cancel=should_cancel)

        def on_result(res: dict):
            try:
                if res.get("cancelled") and not res.get("total"):
                    prev = self.output.toPlainText()
                    self.output.setPlainText(prev + ("\n" if prev else "") + "Cancelled.")
                    return
                if res.get("error"):
                    self.output.setPlainText(f"Run error: {res['error']}")
                    return
                mc = res.get("mapping_counts") or {}
                mc_lines = ", ".join([f"{k}={v}" for k, v in sorted(mc.items())]) or "-"
                lines = [
                    "Batch Anonymization Run:",
                    f"  mode={res.get('mode')} total={res.get('total')} ok={res.get('processed_ok')} skipped={res.get('skipped_existing')} failed={res.get('failed')}",
                    f"  mapping_counts: {mc_lines}",
                    f"  ended_at={res.get('ended_at')}",
                    "  (Deterministic => stable hash tokens; Pseudonymize => random one-off tokens)",
                ]
                # Show first few file statuses for quick feedback
                files = res.get("files") or []
                if files:
                    lines.append("  files (first up to 15):")
                    for f in files[:15]:
                        lines.append(
                            f"    - {f.get('rel')}: {f.get('status')}"
                            + (f" ctx={f.get('context_id')}" if f.get("context_id") else "")
                            + (f" err={f.get('error')}" if f.get("error") else "")
                        )
                if res.get("cancelled"):
                    lines.append("  Note: run was cancelled before completion.")
                prev = self.output.toPlainText()
                nl = "\n" if prev else ""
                self.output.setPlainText(prev + nl + "\n".join(lines))
            finally:
                self._set_busy(False)
                self._current = None

        def on_error(err: str):
            try:
                self.output.setPlainText(f"Run failed: {err}")
            finally:
                self._set_busy(False)
                self._current = None

        t, w = start_worker(job, on_log=self._append_log, on_result=on_result, on_error=on_error)
        self._jobs.append((t, w))
        self._current = (t, w)
