from __future__ import annotations

from typing import Optional

from preprocessing.domain.ports import LlmEnrichmentPort
from .llm_enricher import LlmEnricher
from preprocessing.adapters.enrichment.backends import JsonBackend
from preprocessing.adapters.enrichment.prompts_sk import DEFAULT_PROMPTS


class OpenAiLlmEnricher(LlmEnrichmentPort):
    """Compatibility wrapper around unified LlmEnricher.

    - If the client exposes generate_summary_and_tags, use it directly (for tests/stubs).
    - Otherwise, fall back to JSON chat backend requiring a .chat(...) client method.
    """

    def __init__(self, client, *, model: Optional[str] = None, max_tokens: int = 512) -> None:  # type: ignore[no-redef]
        mdl = model or "gpt-4o-mini"
        if hasattr(client, "generate_summary_and_tags") and callable(getattr(client, "generate_summary_and_tags")):
            # Build a tiny backend that delegates to the provided method
            class _CompatBackend:
                def __init__(self, c, m, mt):
                    self._c = c
                    self._m = m
                    self._mt = int(mt)

                def generate(self, text: str, *, top_k: int):
                    try:
                        s, t = self._c.generate_summary_and_tags(text=text, model=self._m, max_tokens=self._mt, top_k=top_k)
                    except TypeError:
                        try:
                            s, t = self._c.generate_summary_and_tags(text=text, model=self._m, max_tokens=self._mt)
                        except TypeError:
                            s, t = self._c.generate_summary_and_tags(text=text)
                    if not s and not t:
                        raise RuntimeError("LLM returned empty summary and tags")
                    return s, list(t)

            backend = _CompatBackend(client, mdl, max_tokens)
        else:
            backend = JsonBackend(client, model=mdl, max_tokens=max_tokens, prompts=DEFAULT_PROMPTS)
        self._impl = LlmEnricher(backend)

    def summarize(self, text: str) -> str:
        return self._impl.summarize(text)

    def keywords(self, text: str, top_k: int = 10) -> list[str]:
        return self._impl.keywords(text, top_k=top_k)
