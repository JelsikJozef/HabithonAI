from __future__ import annotations

from typing import Optional, Tuple, List, Any

from preprocessing.domain.ports import LlmEnrichmentPort


class SummaryTagger(LlmEnrichmentPort):
    """LLM-based summary and tag generator using a pluggable backend with simple caching.

    - Backend generates both summary and tags in a fail-fast manner.
    - Caches last result keyed by the exact text to avoid duplicate calls
      when summarize() and keywords() are invoked separately on the same text.
    """

    def __init__(self, backend: Any) -> None:
        self._backend = backend
        self._cache_key = None  # type: Optional[str]
        self._cache_value = None  # type: Optional[Tuple[str, List[str]]]

    def _ensure(self, text: str, *, top_k: int = 10) -> Tuple[str, List[str]]:
        if self._cache_key == text and self._cache_value is not None:
            s, tags = self._cache_value
            if top_k is not None and top_k > 0 and len(tags) >= top_k:
                return s, tags
        s, tags = self._backend.generate(text, top_k=max(1, top_k or 1))
        self._cache_key = text
        self._cache_value = (s, tags)
        return s, tags

    def summarize(self, text: str) -> str:
        s, _ = self._ensure(text)
        return s

    def keywords(self, text: str, top_k: int = 10) -> list[str]:
        if top_k is None or top_k <= 0:
            return []
        _, tags = self._ensure(text, top_k=top_k)
        return list(tags)[: top_k]

# Backward-compatible alias
LlmEnricher = SummaryTagger
