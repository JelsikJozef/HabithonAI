from __future__ import annotations

from typing import Optional, List, Tuple
import logging

from .openai_client import OpenAiClient
from ...domain.ports import LlmEnrichmentPort

logger = logging.getLogger(__name__)


class OpenAiLlmEnricher(LlmEnrichmentPort):
    """LLM enricher that calls OpenAI once per input to get summary and tags.

    - No prompt logic here; prompts live in OpenAiClient.generate_summary_and_tags.
    - Caches last result keyed by the exact text string to avoid double calls
      when summarize() and keywords() are invoked separately on the same text.
    - Any failure or empty response raises an error to halt processing.
    """

    def __init__(self, client: OpenAiClient, *, model: Optional[str] = None, max_tokens: int = 512) -> None:
        self._client = client
        self._model = model
        self._max_tokens = int(max_tokens)
        self._cache_key = None  # type: Optional[str]
        self._cache_value = None  # type: Optional[Tuple[str, List[str]]]

    def _ensure(self, text: str) -> Tuple[str, List[str]]:
        if self._cache_key == text and self._cache_value is not None:
            return self._cache_value
        try:
            summary, tags = self._client.generate_summary_and_tags(text=text, model=self._model, max_tokens=self._max_tokens)
        except Exception as e:
            logger.error("OpenAI call failed: %s", e)
            raise
        if not summary and not tags:
            raise RuntimeError("LLM returned empty summary and tags")
        self._cache_key = text
        self._cache_value = (summary, tags)
        return summary, tags

    def summarize(self, text: str) -> str:
        s, _ = self._ensure(text)
        return s or ""

    def keywords(self, text: str, top_k: int = 10) -> list[str]:
        _, tags = self._ensure(text)
        if top_k is None or top_k <= 0:
            return []
        return list(tags)[: top_k]
