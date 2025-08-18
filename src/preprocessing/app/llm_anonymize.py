from __future__ import annotations

import hashlib
from typing import Tuple

from ..domain.models import ParsedDocument


class LlmAnonymisationService:
    """Provides pseudonymization and de-anonymization helpers for LLM flow.

    - Uses detectors + vault provided at construction time (instantiated once per pipeline).
    - Context ID: prefer doc.hash; fallback to SHA-256 of doc.text when missing.
    """

    def __init__(self, detectors, vault) -> None:
        self._detectors = detectors
        self._vault = vault

    def _context_id(self, doc: ParsedDocument, explicit_ctx: str | None = None) -> str:
        if explicit_ctx:
            return explicit_ctx
        if doc.hash:
            return doc.hash
        # Fallback: deterministic hash of text
        h = hashlib.sha256((doc.text or "").encode("utf-8", errors="ignore")).hexdigest()
        return h

    def pseudonymize_text(self, doc: ParsedDocument, context_id: str | None = None) -> Tuple[str, str]:
        from anonymization.app.pseudonymize import pseudonymize as _pseudonymize

        ctx = self._context_id(doc, explicit_ctx=context_id)
        res = _pseudonymize(doc.text or "", self._detectors, self._vault, ctx, language=doc.language)
        return res.pseudonymized_text, ctx

    def deanonymize_output(self, text: str, context_id: str) -> str:
        from anonymization.app.denomize import deanonymize as _deanonymize

        res = _deanonymize(text, self._vault, context_id)
        return res.restored_text

