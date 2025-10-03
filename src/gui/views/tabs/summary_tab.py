from __future__ import annotations

from typing import Any
from pathlib import Path

from ..qt import (
    QWidget,
    QVBoxLayout,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QFileDialog,
    QComboBox,
    QCheckBox,
    QLabel,
)
from ..ui_helpers import create_field_label
from ...services.facade import GuiServices
from ...services.async_worker import start_worker

# Import model discovery helper
from shared.llm.openai_client import get_available_gpt5_models


_OPENAI_MODELS = [
    # Default first (valid OpenAI model)
    "gpt-4o-mini",
    # Other common models; editable combo allows custom
    "gpt-4o",
    "gpt-4.1-mini",
    "gpt-4.1",
    "gpt-3.5-turbo",
    # Legacy/custom placeholders can be typed manually if needed
]


class SummaryTab(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.svc = GuiServices()
        self._current: tuple | None = None
        self._jobs: list[tuple] = []

        vbox = QVBoxLayout(self)

        # --- By Document UID (Step 1 artifacts present) ---
        form_uid = QFormLayout()
        title1 = QLabel("Run on Step 1 document UID")
        vbox.addWidget(title1)

        self.uid_edit = QLineEdit()
        self.uid_edit.setPlaceholderText("document_uid (from Step 1)")
        form_uid.addRow(
            create_field_label("Document UID:", "Unique ID produced by Step 1 (e.g., doc_xxx)"),
            self.uid_edit,
        )

        self.hash_edit = QLineEdit()
        self.hash_edit.setPlaceholderText("Optional content_hash for validation")
        form_uid.addRow(
            create_field_label("Content hash:", "Optional: ensures integrity; must match Step 1."),
            self.hash_edit,
        )

        # Model dropdown (editable) + Test/Refresh buttons
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        try:
            models = get_available_gpt5_models() or []
            # Ensure some sensible defaults if discovery returned nothing
            if not models:
                models = ["gpt-5-mini", "gpt-5", "gpt-4o-mini"]
            self.model_combo.addItems(models)
            self.model_combo.setCurrentText(models[0])
        except Exception:
            try:
                # Fallback static list
                self.model_combo.addItems(["gpt-5-mini", "gpt-4o-mini"])
                self.model_combo.setCurrentText("gpt-5-mini")
            except Exception:
                pass

        # Buttons to test and refresh models
        self.test_model_btn = QPushButton("Test Model")
        self.refresh_models_btn = QPushButton("Refresh Models")
        row_model = QHBoxLayout()
        row_model.addWidget(self.model_combo, 1)
        row_model.addWidget(self.test_model_btn)
        row_model.addWidget(self.refresh_models_btn)
        form_uid.addRow("Model:", row_model)

        self.timeout_edit = QLineEdit()
        self.timeout_edit.setPlaceholderText("Timeout seconds (default: 60)")
        form_uid.addRow("Timeout (s):", self.timeout_edit)

        vbox.addLayout(form_uid)

        # Actions for UID
        row_uid = QHBoxLayout()
        self.run_uid_btn = QPushButton("Run LLM Summary (UID)")
        self.cancel_btn = QPushButton("Cancel")
        self.open_uid_btn = QPushButton("Open Artifacts…")
        self.cancel_btn.setEnabled(False)
        row_uid.addWidget(self.run_uid_btn)
        row_uid.addWidget(self.cancel_btn)
        row_uid.addStretch(1)
        row_uid.addWidget(self.open_uid_btn)
        vbox.addLayout(row_uid)

        # --- Batch on Anonymized Folder ---
        vbox.addWidget(QLabel("\nRun on folder of anonymized Markdown (.anonymized.md)"))
        form_dir = QFormLayout()
        self.src_edit = QLineEdit()
        self.src_edit.setPlaceholderText("Select folder with anonymized .md files")
        pick_src = QPushButton("Browse…")
        row_src = QHBoxLayout()
        row_src.addWidget(self.src_edit, 1)
        row_src.addWidget(pick_src)
        form_dir.addRow("Source folder:", row_src)

        self.pattern_edit = QLineEdit("*.anonymized.md")
        form_dir.addRow("Filename pattern:", self.pattern_edit)
        self.overwrite_cb = QCheckBox("Overwrite existing outputs")
        self.overwrite_cb.setChecked(True)
        form_dir.addRow(self.overwrite_cb)
        vbox.addLayout(form_dir)

        row_dir = QHBoxLayout()
        self.run_dir_btn = QPushButton("Run LLM Summary (Folder)")
        row_dir.addWidget(self.run_dir_btn)
        row_dir.addStretch(1)
        vbox.addLayout(row_dir)

        # Output area
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText(
            "Enter document UID or select a source folder, then run to generate summary/keywords…"
        )
        vbox.addWidget(self.output, 1)

        # Signals
        self.run_uid_btn.clicked.connect(self._on_run_uid)
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.open_uid_btn.clicked.connect(self._on_open_uid)
        pick_src.clicked.connect(self._on_browse_src)
        self.run_dir_btn.clicked.connect(self._on_run_dir)
        self.test_model_btn.clicked.connect(self._on_test_model)
        self.refresh_models_btn.clicked.connect(self._on_refresh_models)

    def _set_busy(self, busy: bool) -> None:
        for w in [
            self.run_uid_btn,
            self.run_dir_btn,
            self.uid_edit,
            self.hash_edit,
            self.model_combo,
            self.timeout_edit,
            self.open_uid_btn,
            self.src_edit,
            self.pattern_edit,
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

    def _append(self, line: str) -> None:
        prev = self.output.toPlainText()
        nl = "\n" if prev else ""
        self.output.setPlainText(prev + nl + str(line))

    def _on_open_uid(self) -> None:
        uid = self.uid_edit.text().strip()
        if not uid:
            self._append("Enter a document UID first.")
            return
        step3_dir = Path("outputs") / "artifacts" / uid / "step3"
        try:
            step3_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        d = QFileDialog.getExistingDirectory(self, "Open Step 3 artifacts", str(step3_dir))
        if d:
            self._append(f"Opened: {d}")

    def _on_browse_src(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select source folder")
        if d:
            self.src_edit.setText(d)

    def _on_cancel(self) -> None:
        if self._current and len(self._current) == 2:
            _t, w = self._current
            try:
                w.cancel()
                self._append("Cancel requested…")
            except Exception:
                pass
        self.cancel_btn.setEnabled(False)

    def _on_run_uid(self) -> None:
        uid = self.uid_edit.text().strip()
        if not uid:
            self.output.setPlainText("Enter a document UID (from Step 1).")
            return
        content_hash = self.hash_edit.text().strip() or None
        model = self.model_combo.currentText().strip() or None
        timeout: int | None = None
        ts = self.timeout_edit.text().strip()
        if ts.isdigit():
            try:
                timeout = int(ts)
            except Exception:
                timeout = None

        self.output.setPlainText("")
        self._set_busy(True)

        def job(progress=None, should_cancel=None):
            return self.svc.step3_run(
                uid,
                content_hash=content_hash,
                model=model,
                timeout_s=timeout,
                progress=progress,
                should_cancel=should_cancel,
            )

        def on_result(res: dict[str, Any]):
            try:
                if res.get("cancelled"):
                    self._append("Cancelled.")
                    return
                if res.get("error"):
                    self.output.setPlainText(f"Step 3 failed: {res['error']}")
                    return
                status = res.get("status")
                if status != "ok":
                    errs = ", ".join(
                        e.get("code", "?") for e in (res.get("errors") or []) if isinstance(e, dict)
                    )
                    self.output.setPlainText(f"Step 3 failed: {errs}")
                    return
                # On success, show safe artifacts
                uid_local = str(res.get("document_uid") or uid)
                step3_dir = Path("outputs") / "artifacts" / uid_local / "step3"
                s_text = None
                k_list = None
                try:
                    sp = step3_dir / "summary.txt"
                    if sp.exists():
                        s_text = sp.read_text(encoding="utf-8")
                except Exception:
                    s_text = None
                try:
                    kp = step3_dir / "keywords.json"
                    if kp.exists():
                        import json as _json

                        k_list = _json.loads(kp.read_text(encoding="utf-8"))
                except Exception:
                    k_list = None
                lines = ["Step 3 finished: OK."]
                if s_text:
                    lines += ["", "Summary (one sentence):", s_text.strip()]
                if isinstance(k_list, list):
                    lines += ["", "Top 5 keywords:"] + ["  - " + str(x) for x in k_list]
                self.output.setPlainText("\n".join(lines))
            finally:
                self._set_busy(False)
                self._current = None

        def on_error(err: str):
            try:
                self.output.setPlainText(f"Step 3 error: {err}")
            finally:
                self._set_busy(False)
                self._current = None

        t, w = start_worker(job, on_log=self._append, on_result=on_result, on_error=on_error)
        self._jobs.append((t, w))
        self._current = (t, w)

    def _on_run_dir(self) -> None:
        src = self.src_edit.text().strip()
        if not src:
            self.output.setPlainText("Select a source folder with anonymized .md files.")
            return
        pattern = self.pattern_edit.text().strip() or "*.anonymized.md"
        overwrite = bool(self.overwrite_cb.isChecked())
        model = self.model_combo.currentText().strip() or None
        timeout: int | None = None
        ts = self.timeout_edit.text().strip()
        if ts.isdigit():
            try:
                timeout = int(ts)
            except Exception:
                timeout = None

        self.output.setPlainText("")
        self._set_busy(True)

        def job(progress=None, should_cancel=None):
            return self.svc.summarize_folder_run(
                {
                    "src": src,
                    "pattern": pattern,
                    "overwrite": overwrite,
                    "model": model,
                    "timeout_s": timeout,
                },
                progress=progress,
                should_cancel=should_cancel,
            )

        def on_result(res: dict[str, Any]):
            try:
                if res.get("cancelled") and not res.get("total"):
                    self._append("Cancelled.")
                    return
                if err := res.get("error"):
                    self.output.setPlainText(f"Run failed: {err}")
                    return
                lines = [
                    "LLM Summary (folder) run:",
                    f"  total={res.get('total')} ok={res.get('ok')} skipped={res.get('skipped')} failed={res.get('failed')}",
                    f"  ended_at={res.get('ended_at')}",
                ]
                # Show first few file statuses
                files = res.get("files") or []
                if files:
                    lines.append("  files (first up to 20):")
                    for f in files[:20]:
                        lines.append(
                            f"    - {f.get('rel')}: {f.get('status')}"
                            + (f" err={f.get('error')}" if f.get("error") else "")
                        )
                if res.get("cancelled"):
                    lines.append("  Note: run was cancelled before completion.")
                self.output.setPlainText("\n".join(lines))
            finally:
                self._set_busy(False)
                self._current = None

        def on_error(err: str):
            try:
                self.output.setPlainText(f"Run error: {err}")
            finally:
                self._set_busy(False)
                self._current = None

        t, w = start_worker(job, on_log=self._append, on_result=on_result, on_error=on_error)
        self._jobs.append((t, w))
        self._current = (t, w)

    def _on_refresh_models(self) -> None:
        """Reload available GPT-5 models and update the combo box."""
        try:
            self._append("Refreshing model list…")
            models = get_available_gpt5_models() or []
            if not models:
                models = ["gpt-5-mini", "gpt-5", "gpt-4o-mini"]
            cur = (
                self.model_combo.currentText() if hasattr(self.model_combo, "currentText") else None
            )
            self.model_combo.clear()
            self.model_combo.addItems(models)
            if cur and cur.strip():
                # restore if still present, otherwise keep first
                try:
                    if cur in models:
                        self.model_combo.setCurrentText(cur)
                    else:
                        self.model_combo.setCurrentText(models[0])
                except Exception:
                    pass
            self._append(f"Model list refreshed: {len(models)} candidates")
        except Exception as e:
            self._append(f"Failed to refresh models: {e}")

    def _on_test_model(self) -> None:
        """Run a lightweight probe against the currently selected model."""
        model = (
            self.model_combo.currentText().strip()
            if hasattr(self.model_combo, "currentText")
            else ""
        )
        if not model:
            self._append("Select a model first.")
            return
        self._set_busy(True)
        self.output.setPlainText("")

        def job(progress=None, should_cancel=None):
            return self.svc.test_model(model, timeout_s=30)

        def on_result(res: dict[str, Any]):
            try:
                if res.get("status") == "ok":
                    s = res.get("summary") or "(no summary)"
                    kws = res.get("keywords") or []
                    lines = [f"Model test OK: {model}", f"Summary: {s}", "Keywords:"] + [
                        f" - {k}" for k in kws
                    ]
                    self.output.setPlainText("\n".join(lines))
                else:
                    err = res.get("error") or "unknown error"
                    self.output.setPlainText(f"Model test FAILED: {model} -> {err}")
            finally:
                self._set_busy(False)

        def on_error(err: str):
            try:
                self.output.setPlainText(f"Model test error: {err}")
            finally:
                self._set_busy(False)

        t, w = start_worker(job, on_log=self._append, on_result=on_result, on_error=on_error)
        self._jobs.append((t, w))
        self._current = (t, w)

    def cancel_all_jobs(self) -> None:
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
