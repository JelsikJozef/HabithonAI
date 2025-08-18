from __future__ import annotations

from ..domain.models import ParsedDocument
from ..domain.ports import EnrichmentPort


class MetadataEnrichmentService:
    """Heuristic metadata enrichment (non-LLM).

    Delegates to the provided EnrichmentPort to enrich language, hash, tokens, counts, etc.
    """

    def __init__(self, enricher: EnrichmentPort) -> None:
        self._enricher = enricher

    def run(self, doc: ParsedDocument) -> ParsedDocument:
        return self._enricher.enrich(doc)

