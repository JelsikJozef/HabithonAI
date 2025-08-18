from __future__ import annotations

from typing import Any

from ...domain.ports import LlmEnrichmentPort


class LlmEnricher(LlmEnrichmentPort):
    """Thin wrapper over an LLM client for summary and keywords.

    - No logging of input content; caller/client should handle retries/timeouts.
    - Tries client.complete(prompt=..., model=..., max_tokens=...) or client.generate(...),
      otherwise treats the client as a callable taking the prompt and returning text.
    """

    def __init__(self, client: Any, *, model: str, max_tokens: int = 512) -> None:
        if not isinstance(model, str) or not model:
            raise ValueError("model must be a non-empty string")
        if max_tokens <= 0:
            raise ValueError("max_tokens must be > 0")
        self._client = client
        self._model = model
        self._max_tokens = int(max_tokens)

    # Port API (by text input)
    def summarize(self, text: str) -> str:
        prompt = self._prompt_summarize(text)
        out = self._complete(prompt)
        return (out or "").strip()

    def keywords(self, text: str, top_k: int = 10) -> list[str]:
        if top_k <= 0:
            return []
        prompt = self._prompt_keywords(text, top_k)
        out = self._complete(prompt)
        items = []
        if out:
            # Accept comma or newline separated; preserve order and uniqueness
            raw = [p for line in out.splitlines() for p in line.split(",")]
            seen = set()
            for t in raw:
                s = t.strip().strip("-•* ")
                if not s:
                    continue
                if s.lower() in seen:
                    continue
                seen.add(s.lower())
                items.append(s)
        return items[:top_k]

    # Internal helpers
    def _complete(self, prompt: str) -> str:
        c = self._client
        # Try common client shapes without leaking content
        try:
            if hasattr(c, "complete") and callable(getattr(c, "complete")):
                return str(c.complete(prompt=prompt, model=self._model, max_tokens=self._max_tokens))
            if hasattr(c, "generate") and callable(getattr(c, "generate")):
                return str(c.generate(prompt=prompt, model=self._model, max_tokens=self._max_tokens))
            if callable(c):
                return str(c(prompt))
        except Exception:
            # Let caller manage retries/timeouts; return empty on failure
            return ""
        return ""

    def _prompt_summarize(self, text: str) -> str:
        return (
            "Si stručný asistent. Zhrň nasledujúci text do 1–3 viet v slovenskom jazyku. "
            "Nevkladaj úvodné ani záverečné poznámky. VÝSTUP: iba samotné zhrnutie.\n\n"
            "Dôležité: Ak text obsahuje špeciálne PII tokeny vo formáte {{PII:...}}, zachovaj ich presne tak, ako sú (bez zmien).\n\n"
            "Text:\n" + text
        )

    def _prompt_keywords(self, text: str, top_k: int) -> str:
        return (
            "Extrahuj top {k} stručných kľúčových slov alebo krátkych fráz zo vstupu v slovenskom jazyku. "
            "Vráť jednoduchý zoznam oddelený čiarkami, bez číslovania a bez dodatočného textu.\n\n"
            "Dôležité: Ak text obsahuje špeciálne PII tokeny vo formáte {{PII:...}}, zachovaj ich presne tak, ako sú (bez zmien).\n\n"
            "Text:\n"
        ).format(k=top_k) + text
