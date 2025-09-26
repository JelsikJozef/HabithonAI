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


class AnonBatchTab(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.svc = GuiServices()

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
        self.plan_btn.setToolTip("Dry summary: counts of files that would be processed / skipped.")
        self.run_btn.setToolTip("Execute batch anonymization.")
        actions.addWidget(wrap_with_help(self.plan_btn, "Dry summary of files to process or skip."))
        actions.addWidget(
            wrap_with_help(self.run_btn, "Execute batch anonymization (writes output files).")
        )
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
        self.mode_combo.currentTextChanged.connect(self._update_enabled)

        self._update_enabled()

    # --- Helpers ------------------------------------------------------------
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
    def _on_plan(self) -> None:
        try:
            cfg = dict(self._cfg())
            if not cfg.get("src") or not cfg.get("out"):
                self.output.setPlainText("Select source and output folders first.")
                return
            res = self.svc.anon_batch_plan(cfg)
            if res.get("error"):
                self.output.setPlainText(f"Plan error: {res['error']}")
                return
            lines = [
                "Batch Anonymization Plan:",
                f"  mode={res.get('mode')} recurse={cfg.get('recurse')} overwrite={cfg.get('overwrite')}",
                f"  matched={res.get('matched')} would_process={res.get('would_process')} would_skip_existing={res.get('would_skip_existing')}",
                "  (Deterministic => stable hash tokens; Pseudonymize => random one-off tokens)",
            ]
            self.output.setPlainText("\n".join(lines))
        except Exception as e:  # pragma: no cover - UI safety
            self.output.setPlainText(f"Plan failed: {e}")

    def _on_run(self) -> None:
        try:
            cfg = dict(self._cfg())
            if not cfg.get("src") or not cfg.get("out"):
                self.output.setPlainText("Select source and output folders first.")
                return
            res = self.svc.anon_batch_run(cfg)
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
            self.output.setPlainText("\n".join(lines))
        except Exception as e:  # pragma: no cover - UI safety
            self.output.setPlainText(f"Run failed: {e}")
