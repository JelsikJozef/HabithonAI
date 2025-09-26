from __future__ import annotations

from ..qt import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLineEdit,
    QTextEdit,
    QPushButton,
    QComboBox,
    QFileDialog,
)

from ...services.facade import GuiServices
from shared.hashing import document_fingerprint  # stable context id from file
from ..ui_helpers import auto_expand_combo, wrap_with_help, create_field_label  # new imports


class AnonymizationTab(QWidget):
    """Interactive playground for detection/pseudonymization/de-anonymization.

    Now supports selecting a Markdown file from Finder and operating on its content.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.svc = GuiServices()
        self._last_result: dict | None = None
        self._opened_path: str | None = None

        vbox = QVBoxLayout(self)

        # File row
        file_row = QHBoxLayout()
        self.file_edit = QLineEdit()
        self.file_edit.setPlaceholderText("No file selected (.md)")
        self.file_edit.setReadOnly(True)
        self.open_btn = QPushButton("Open .md…")
        self.save_btn = QPushButton("Save output…")
        file_row.addWidget(self.file_edit, 1)
        file_row.addWidget(self.open_btn)
        file_row.addWidget(self.save_btn)
        vbox.addLayout(file_row)

        # Inputs
        form = QFormLayout()
        self.lang_combo = QComboBox()
        self.lang_combo.setEditable(True)
        self.lang_combo.addItems(["", "en", "sk", "de", "cs", "pl", "hu"])  # quick presets
        auto_expand_combo(self.lang_combo)
        self.ctx_edit = QLineEdit()
        self.ctx_edit.setPlaceholderText("Context ID (required for pseudo/deanonymize)")
        self.tenant_edit = QLineEdit()
        self.tenant_edit.setPlaceholderText("Tenant ID (optional; scopes deterministic anonymize)")
        form.addRow(
            create_field_label(
                "Language:", "Optional language hint to improve detector precision."
            ),
            self.lang_combo,
        )
        form.addRow(
            create_field_label(
                "Context ID:", "Identifier to namespace token mappings (per document)."
            ),
            self.ctx_edit,
        )
        form.addRow(
            create_field_label(
                "Tenant ID:", "Scopes deterministic hashing; different tenant -> different tokens."
            ),
            self.tenant_edit,
        )
        vbox.addLayout(form)

        self.input_text = QTextEdit()
        self.input_text.setPlaceholderText(
            "Paste or type sample text containing PII… or Open a .md file above"
        )
        vbox.addWidget(self.input_text, 1)

        # Actions
        actions = QHBoxLayout()
        self.detect_btn = QPushButton("Detect")
        self.detect_btn.setToolTip("Run PII detectors and show merged entities.")
        self.pseudo_btn = QPushButton("Pseudonymize")
        self.pseudo_btn.setToolTip(
            "Replace PII with random structured tokens (not stable across files)."
        )
        self.de_btn = QPushButton("De-anonymize")
        self.de_btn.setToolTip(
            "Restore original text from previously saved mappings for the context."
        )
        self.anonym_btn = QPushButton("Deterministic anonymize")
        self.anonym_btn.setToolTip(
            "Replace PII with stable HMAC tokens (same value -> same token)."
        )
        self.presidio_btn = QPushButton("Check Presidio")
        self.presidio_btn.setToolTip("Inspect Presidio / spaCy readiness and model availability.")
        for w, tip in [
            (self.detect_btn, None),
            (self.pseudo_btn, None),
            (self.de_btn, None),
            (self.anonym_btn, None),
            (self.presidio_btn, None),
        ]:
            actions.addWidget(wrap_with_help(w, w.toolTip() or "Action"))
        actions.addStretch(1)
        vbox.addLayout(actions)

        # Output
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        vbox.addWidget(self.output, 1)

        # Wire
        self.open_btn.clicked.connect(self._on_open_file)
        self.save_btn.clicked.connect(self._on_save_output)
        self.detect_btn.clicked.connect(self._on_detect)
        self.pseudo_btn.clicked.connect(self._on_pseudo)
        self.de_btn.clicked.connect(self._on_de)
        self.anonym_btn.clicked.connect(self._on_anonym)
        self.presidio_btn.clicked.connect(self._on_presidio_check)

    # --- Helpers / formatting -------------------------------------------------
    def _lang(self) -> str | None:
        s = self.lang_combo.currentText().strip()
        return s or None

    def _ctx(self) -> str:
        return self.ctx_edit.text().strip()

    def _tenant(self) -> str | None:
        s = self.tenant_edit.text().strip()
        return s or None

    def _derive_context_from_file(self, path: str) -> str:
        try:
            import os

            st = os.stat(path)
            # Use fingerprint over (relative path, size, mtime) for a stable ID
            fp = document_fingerprint(path, size_bytes=st.st_size, mtime=st.st_mtime)
            return f"ctx_{fp[:16]}"
        except Exception:
            # Fallback to basename
            import os

            return f"ctx_{os.path.basename(path)}"

    def _fmt_section(self, title: str) -> str:
        return f"\n{'=' * 8} {title} {'=' * 8}\n"

    def _fmt_detectors(self, detectors: list[dict] | None) -> str:
        if not detectors:
            return "(no detector metadata)"
        lines: list[str] = []
        for d in detectors:
            name = d.get("name")
            if name == "presidio":
                langs = ",".join(d.get("supported_languages") or []) or "-"
                fb = d.get("fallback_language") or "-"
                init_err = d.get("init_error")
                regex_fb = d.get("regex_fallback_enabled")
                lines.append(
                    f"presidio: langs=[{langs}] fallback={fb} regex_fallback={'on' if regex_fb else 'off'}"
                    + (f" INIT_ERROR={init_err}" if init_err else "")
                )
            elif name == "regex":
                pats = ",".join(d.get("patterns") or [])
                lines.append(f"regex: patterns=[{pats}]")
            else:
                lines.append(str(d))
        return "\n".join(lines)

    def _fmt_entities(self, entities: list[dict] | None) -> str:
        if not entities:
            return "(no entities)"
        header = (
            f"{'#':>3}  {'TYPE':<18} {'DETECTOR':<10} {'START':>5} {'END':>5} {'SCORE':>5}  VALUE"
        )
        rows = [header, "-" * len(header)]
        for i, e in enumerate(entities, 1):
            raw_val = e.get("value", "")
            if not isinstance(raw_val, str):
                raw_val = str(raw_val)
            val = raw_val.replace("\n", " ")
            score = e.get("score")
            score_txt = f"{score:.2f}" if isinstance(score, (int, float)) else "-"
            typ = (e.get("type", "") or "")[:18]
            det = (str(e.get("detector", "")) or "")[:10]
            start = e.get("start")
            end = e.get("end")
            rows.append(f"{i:>3}  {typ:<18} {det:<10} {start:>5} {end:>5} {score_txt:>5}  {val}")
        return "\n".join(rows)

    def _fmt_mappings(self, mappings: list[dict] | None, max_items: int = 50) -> str:
        if not mappings:
            return "(no mappings)"
        header = f"{'#':>3}  {'TYPE':<18} {'TOKEN':<32} VALUE"
        rows = [header, "-" * len(header)]
        for i, m in enumerate(mappings[:max_items], 1):
            raw_val = m.get("value", "")
            if not isinstance(raw_val, str):
                raw_val = str(raw_val)
            val = raw_val.replace("\n", " ")
            rows.append(f"{i:>3}  {m.get('type','')[:18]:<18} {m.get('token','')[:32]:<32} {val}")
        if len(mappings) > max_items:
            rows.append(f"... ({len(mappings)-max_items} more)")
        return "\n".join(rows)

    # --- File actions ---------------------------------------------------------
    def _on_open_file(self) -> None:
        try:
            path, _flt = QFileDialog.getOpenFileName(
                self, "Open Markdown file", "", "Markdown (*.md);;All Files (*)"
            )
        except Exception:
            path = ""
        if not path:
            return
        self._opened_path = path
        self.file_edit.setText(path)
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            self.input_text.setPlainText(text)
            # Auto-derive context id if empty
            if not self._ctx():
                self.ctx_edit.setText(self._derive_context_from_file(path))
            self.output.setPlainText(
                "Loaded Markdown file. Choose an action (Detect/Pseudonymize/Deterministic anonymize)."
            )
            self._last_result = None
        except Exception as e:
            self.output.setPlainText(f"Failed to open file: {e}")

    # --- Save output ----------------------------------------------------------
    def _resolve_save_text(self) -> tuple[str, str]:
        """Return (text, default_suffix) to save based on last action."""
        if isinstance(self._last_result, dict):
            if self._last_result.get("pseudonymized_text"):
                return self._last_result["pseudonymized_text"], ".pseudonymized.md"
            if self._last_result.get("anonymized_text"):
                return self._last_result["anonymized_text"], ".anonymized.md"
            if self._last_result.get("restored_text"):
                return self._last_result["restored_text"], ".restored.md"
        # Fallback to input text
        return self.input_text.toPlainText(), ".txt"

    def _on_save_output(self) -> None:
        text, suffix = self._resolve_save_text()
        if not text:
            self.output.setPlainText("Nothing to save yet. Run an action first.")
            return
        # Default path next to opened file if available
        start_dir = ""
        default_name = "output" + suffix
        if self._opened_path:
            import os

            start_dir = os.path.dirname(self._opened_path)
            base = os.path.splitext(os.path.basename(self._opened_path))[0]
            default_name = base + suffix
        try:
            path, _flt = QFileDialog.getSaveFileName(
                self, "Save output", f"{start_dir}/{default_name}", "Markdown (*.md);;All Files (*)"
            )
        except Exception:
            path = ""
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            self.output.setPlainText(f"Saved output to: {path}")
        except Exception as e:
            self.output.setPlainText(f"Save failed: {e}")

    # --- Actions --------------------------------------------------------------
    def _on_detect(self) -> None:
        try:
            text = self.input_text.toPlainText()
            res = self.svc.anon_detect(text, language=self._lang())
            ents = res.get("entities") or []
            counts = res.get("detector_counts") or {}
            detectors_meta = self._fmt_detectors(res.get("detectors"))
            summary = ", ".join([f"{k}={v}" for k, v in sorted(counts.items())]) or "-"
            out_parts = [
                self._fmt_section("DETECTION"),
                f"Language hint: {res.get('language_hint') or '-'}",
                f"Detectors: {detectors_meta}",
                f"Entities total: {len(ents)} (per detector: {summary})",
                self._fmt_section("ENTITIES"),
                self._fmt_entities(ents),
            ]
            self.output.setPlainText("\n".join(out_parts))
            self._last_result = res
        except Exception as e:
            self.output.setPlainText(f"Detect failed: {e}")

    def _on_pseudo(self) -> None:
        ctx = self._ctx()
        if not ctx:
            self.output.setPlainText("Context ID is required for pseudonymize.")
            return
        try:
            text = self.input_text.toPlainText()
            res = self.svc.anon_pseudonymize(text, context_id=ctx, language=self._lang())
            mappings = res.get("mappings") or []
            map_counts = res.get("mapping_counts") or {}
            detectors_meta = self._fmt_detectors(res.get("detectors"))
            counts_summary = ", ".join([f"{k}={v}" for k, v in sorted(map_counts.items())]) or "-"
            out_parts = [
                self._fmt_section("PSEUDONYMIZATION"),
                f"Context: {res.get('context_id')}  Language: {res.get('language_hint') or '-'}",
                f"Detectors: {detectors_meta}",
                f"Mappings: {len(mappings)} (by type: {counts_summary})",
                self._fmt_section("OUTPUT TEXT"),
                res.get("pseudonymized_text", ""),
                self._fmt_section("MAPPINGS"),
                self._fmt_mappings(mappings),
            ]
            self.output.setPlainText("\n".join(out_parts))
            self._last_result = res
        except Exception as e:
            self.output.setPlainText(f"Pseudonymize failed: {e}")

    def _on_de(self) -> None:
        ctx = self._ctx()
        if not ctx:
            self.output.setPlainText("Context ID is required for de-anonymize.")
            return
        try:
            text = self.input_text.toPlainText()
            res = self.svc.anon_deanonymize(text, context_id=ctx)
            used = res.get("mappings_used") or []
            map_counts = res.get("mapping_counts") or {}
            counts_summary = ", ".join([f"{k}={v}" for k, v in sorted(map_counts.items())]) or "-"
            out_parts = [
                self._fmt_section("DE-ANONYMIZATION"),
                f"Context: {res.get('context_id')}",
                f"Mappings used: {len(used)} (by type: {counts_summary})",
                self._fmt_section("RESTORED TEXT"),
                res.get("restored_text", ""),
                self._fmt_section("MAPPINGS USED"),
                self._fmt_mappings(used),
            ]
            self.output.setPlainText("\n".join(out_parts))
            self._last_result = res
        except Exception as e:
            self.output.setPlainText(f"De-anonymize failed: {e}")

    def _on_anonym(self) -> None:
        ctx = self._ctx()
        if not ctx:
            self.output.setPlainText("Context ID is required for deterministic anonymize.")
            return
        try:
            text = self.input_text.toPlainText()
            res = self.svc.anon_anonymize(
                text,
                context_id=ctx,
                tenant_id=self._tenant(),
                language=self._lang(),
            )
            mappings = res.get("mappings") or []
            map_counts = res.get("mapping_counts") or {}
            detectors_meta = self._fmt_detectors(res.get("detectors"))
            counts_summary = ", ".join([f"{k}={v}" for k, v in sorted(map_counts.items())]) or "-"
            out_parts = [
                self._fmt_section("DETERMINISTIC ANONYMIZATION"),
                f"Context: {res.get('context_id')}  Tenant: {res.get('tenant_id') or '-'}  Lang: {res.get('language_hint') or '-'}",
                f"Detectors: {detectors_meta}",
                f"Mappings persisted: {len(mappings)} (by type: {counts_summary})",
                self._fmt_section("ANONYMIZED TEXT"),
                res.get("anonymized_text", ""),
                self._fmt_section("MAPPINGS"),
                self._fmt_mappings(mappings),
            ]
            self.output.setPlainText("\n".join(out_parts))
            self._last_result = res
        except Exception as e:
            self.output.setPlainText(f"Deterministic anonymize failed: {e}")

    def _fmt_presidio_readiness(self, diag: dict | None) -> str:
        if not diag:
            return "(no diagnostics)"
        lines: list[str] = []
        lines.append(f"Presidio imported: {diag.get('presidio_imported')}")
        if diag.get("presidio_error"):
            lines.append(f"  presidio_error: {diag.get('presidio_error')}")
        lines.append(f"spaCy imported: {diag.get('spacy_imported')}")
        if diag.get("spacy_error"):
            lines.append(f"  spacy_error: {diag.get('spacy_error')}")
        lines.append(
            f"Configured languages: {', '.join(diag.get('supported_languages_configured') or [])}"
        )
        lines.append(f"Fallback model: {diag.get('fallback_model')}")
        lines.append(f"Regex fallback enabled: {diag.get('regex_fallback_enabled')}")
        # Models
        lines.append("Models:")
        for m in diag.get("models", []):
            name = m.get("name")
            installed = m.get("installed")
            err = m.get("error")
            lines.append(
                f"  - {name}: {'OK' if installed else 'MISSING'}" + (f" ({err})" if err else "")
            )
        lines.append(f"READY: {diag.get('ready')}")
        sugg = diag.get("suggested_commands") or []
        if sugg:
            lines.append("Suggested commands:")
            for c in sugg:
                lines.append(f"  $ {c}")
        env = diag.get("env") or {}
        if env:
            lines.append("Env overrides:")
            for k, v in env.items():
                lines.append(f"  {k}={v}")
        return "\n".join(lines)

    def _on_presidio_check(self) -> None:
        try:
            diag = self.svc.anon_presidio_readiness()
            out = [
                self._fmt_section("PRESIDIO READINESS"),
                self._fmt_presidio_readiness(diag),
            ]
            self.output.setPlainText("\n".join(out))
            self._last_result = diag
        except Exception as e:
            self.output.setPlainText(f"Presidio readiness check failed: {e}")
