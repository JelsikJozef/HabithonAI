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
        from anonymization.domain.entities import TokenMapping as _TokenMapping

        ctx = self._context_id(doc, explicit_ctx=context_id)
        meta = doc.metadata or {}
        # 1) Reuse precomputed pseudonymization when present (ensures single detection across pipeline)
        pre_text = meta.get("text_pseudo")
        pre_ctx = meta.get("anon_context_id")
        if isinstance(pre_text, str) and pre_text != "" and pre_ctx:
            return pre_text, str(pre_ctx)
        # 2) If entities are available, compute pseudo text without re-running detection and save mappings
        ents = meta.get("pii_entities")
        if isinstance(ents, list) and ents:
            text = doc.text or ""
            # Sort and replace left-to-right, using deterministic token scheme (consistent with bridge)
            items = []
            for e in ents:
                try:
                    t = e.get("type") if isinstance(e, dict) else getattr(e, "type", None)
                    s = int(e.get("start", 0)) if isinstance(e, dict) else int(getattr(e, "start", 0))
                    en = int(e.get("end", 0)) if isinstance(e, dict) else int(getattr(e, "end", 0))
                    v = e.get("value") if isinstance(e, dict) else getattr(e, "value", None)
                    if t is None or v is None:
                        continue
                    items.append({"type": t, "start": s, "end": en, "value": v})
                except Exception:
                    continue
            items.sort(key=lambda x: x["start"])  # type: ignore[no-any-return]
            out_parts = []
            mappings = []
            cursor = 0
            counters: dict[str, int] = {}
            for ent in items:
                s = ent["start"]
                epos = ent["end"]
                if s < cursor:
                    continue
                out_parts.append(text[cursor:s])
                et = ent["type"]
                counters[et] = counters.get(et, 0) + 1
                tok = "{{PII:%s:%d:%s}}" % (et, counters[et], hashlib.sha256((et + str(counters[et])).encode()).hexdigest()[:8])
                out_parts.append(tok)
                mappings.append(_TokenMapping(token=tok, value=ent["value"], type=et))
                cursor = epos
            out_parts.append(text[cursor:])
            pseudo = "".join(out_parts)
            # Persist mappings for this context
            self._vault.save_mappings(ctx, mappings)
            return pseudo, ctx
        # 3) Fallback: run full pseudonymize (will do detection internally)
        res = _pseudonymize(doc.text or "", self._detectors, self._vault, ctx, language=doc.language)
        return res.pseudonymized_text, ctx

    def deanonymize_output(self, text: str, context_id: str) -> str:
        from anonymization.app.denomize import deanonymize as _deanonymize

        res = _deanonymize(text, self._vault, context_id)
        return res.restored_text
