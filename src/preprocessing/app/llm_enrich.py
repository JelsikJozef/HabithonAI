from __future__ import annotations

from dataclasses import replace
from typing import Callable, Optional
import logging

from ..domain.models import ParsedDocument
from ..domain.ports import LlmEnrichmentPort

logger = logging.getLogger(__name__)


class LlmEnrichmentService:
    """Semantic enrichment using an LLM port.

    - summarize(): adds metadata["summary"]
    - keywords(): adds metadata["keywords"] (list[str])

    If configured with anonymization dependencies, the service will:
    - pseudonymize the input text before sending to LLM,
    - instruct the LLM to preserve tokens (prompting handled by adapter),
    - deanonymize the resulting summary/keywords using the same context.
    """

    def __init__(
        self,
        llm: LlmEnrichmentPort,
        *,
        # Optional anonymization hooks (legacy)
        pseudonymize_fn: Optional[Callable[[str, Optional[str]], tuple[str, str]]] = None,
        deanonymize_fn: Optional[Callable[[str, str], str]] = None,
        # Preferred anonymizer service (uses doc.hash for context id)
        anonymizer: Optional[object] = None,
    ) -> None:
        self._llm = llm
        self._pseudo = pseudonymize_fn
        self._deanon = deanonymize_fn
        self._anonymizer = anonymizer

    def _pseudonymize_for_doc(self, doc: ParsedDocument) -> tuple[str, Optional[str]]:
        text = doc.text or ""
        # Prefer the anonymizer service if provided
        if self._anonymizer is not None:
            ctx = doc.hash or ""
            try:
                # Expect anonymizer.pseudonymize_text(doc, context_id) -> (text, context_id)
                pseudo_text, ctx_id = getattr(self._anonymizer, "pseudonymize_text")(doc, ctx)
                return pseudo_text, ctx_id
            except Exception:
                return text, None
        # Fallback to legacy function hooks
        if self._pseudo is not None:
            try:
                ctx_id, pseudo_text = self._pseudo(text, doc.language)
                return pseudo_text, ctx_id
            except Exception:
                return text, None
        return text, None

    def _deanonymize_with_ctx(self, text: str, context_id: Optional[str]) -> str:
        if not context_id:
            return text
        if self._anonymizer is not None:
            try:
                return getattr(self._anonymizer, "deanonymize_output")(text, context_id)
            except Exception:
                return text
        if self._deanon is not None:
            try:
                return self._deanon(text, context_id)
            except Exception:
                return text
        return text

    def summarize(self, doc: ParsedDocument) -> ParsedDocument:
        pseudo_text, ctx_id = self._pseudonymize_for_doc(doc)
        logger.info("LLM summarize: text_len=%d ctx=%s", len(pseudo_text or ""), bool(ctx_id))
        summary = self._llm.summarize(pseudo_text)
        if ctx_id:
            summary = self._deanonymize_with_ctx(summary, ctx_id)
        meta = dict(doc.metadata)
        meta["summary"] = summary
        return replace(doc, metadata=meta)

    def keywords(self, doc: ParsedDocument, top_k: int = 10) -> ParsedDocument:
        pseudo_text, ctx_id = self._pseudonymize_for_doc(doc)
        logger.info("LLM keywords: text_len=%d top_k=%d ctx=%s", len(pseudo_text or ""), int(top_k or 0), bool(ctx_id))
        kws = self._llm.keywords(pseudo_text, top_k=top_k)
        if kws and ctx_id:
            joined = ", ".join(kws)
            joined = self._deanonymize_with_ctx(joined, ctx_id)
            kws = [t.strip() for t in joined.split(",") if t.strip()]
        meta = dict(doc.metadata)
        # Store under both keys for compatibility
        meta["tags"] = list(kws)
        meta["keywords"] = list(kws)
        return replace(doc, metadata=meta)
