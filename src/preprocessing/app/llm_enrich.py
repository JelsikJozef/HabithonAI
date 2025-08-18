from __future__ import annotations

from dataclasses import replace

from ..domain.models import ParsedDocument
from ..domain.ports import LlmEnrichmentPort


class LlmEnrichmentService:
    """Semantic enrichment using an LLM port.

    - summarize(): adds metadata["summary"]
    - keywords(): adds metadata["keywords"] (list[str])
    """

    def __init__(self, llm: LlmEnrichmentPort) -> None:
        self._llm = llm

    def summarize(self, doc: ParsedDocument) -> ParsedDocument:
        summary = self._llm.summarize(doc.text)
        meta = dict(doc.metadata)
        meta["summary"] = summary
        return replace(doc, metadata=meta)

    def keywords(self, doc: ParsedDocument, top_k: int = 10) -> ParsedDocument:
        kws = self._llm.keywords(doc.text, top_k=top_k)
        meta = dict(doc.metadata)
        meta["keywords"] = list(kws)
        return replace(doc, metadata=meta)

