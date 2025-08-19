from __future__ import annotations

from typing import Any, Protocol, Tuple, List, Optional

from .prompts_sk import (
    build_summarize_prompt,
    build_keywords_prompt,
    build_json_messages,
    PromptTexts,
    DEFAULT_PROMPTS,
)


class LlmBackend(Protocol):
    def generate(self, text: str, *, top_k: int) -> Tuple[str, List[str]]:
        """Return (summary, tags) for the given text.

        Implementations must be fail-fast: raise on any error or empty outputs.
        """
        pass


class PromptBackend:
    """Backend that uses plain prompt completions for summary and keywords.

    Expects a client that supports one of:
    - .complete(prompt=..., model=str, max_tokens=int) -> str
    - .generate(prompt=..., model=str, max_tokens=int) -> str
    - Callable(prompt_str) -> str
    """

    def __init__(self, client: Any, *, model: str, max_tokens: int = 512, prompts: Optional[PromptTexts] = None) -> None:
        if not isinstance(model, str) or not model:
            raise ValueError("model must be a non-empty string")
        if max_tokens <= 0:
            raise ValueError("max_tokens must be > 0")
        self._client = client
        self._model = model
        self._max_tokens = int(max_tokens)
        self._prompts = prompts or DEFAULT_PROMPTS

    def _complete(self, prompt: str) -> str:
        c = self._client
        if hasattr(c, "complete") and callable(getattr(c, "complete")):
            return str(c.complete(prompt=prompt, model=self._model, max_tokens=self._max_tokens))
        if hasattr(c, "generate") and callable(getattr(c, "generate")):
            return str(c.generate(prompt=prompt, model=self._model, max_tokens=self._max_tokens))
        if callable(c):
            return str(c(prompt))
        raise RuntimeError("Unsupported LLM client interface for PromptBackend")

    def _parse_keywords(self, out: str, *, top_k: int) -> List[str]:
        items = []  # type: List[str]
        if out:
            raw = [p for line in out.splitlines() for p in line.split(",")]
            seen = set()
            for t in raw:
                s = t.strip().strip("-•* ")
                if not s:
                    continue
                key = s.lower()
                if key in seen:
                    continue
                seen.add(key)
                items.append(s)
        if not items:
            raise RuntimeError("LLM returned empty keywords")
        return items[: max(1, top_k)]

    def generate(self, text: str, *, top_k: int) -> Tuple[str, List[str]]:
        if top_k <= 0:
            top_k = 1
        s_prompt = build_summarize_prompt(text, self._prompts)
        k_prompt = build_keywords_prompt(text, top_k, self._prompts)
        summary = (self._complete(s_prompt) or "").strip()
        if not summary:
            raise RuntimeError("LLM returned empty summary")
        kw_raw = (self._complete(k_prompt) or "").strip()
        tags = self._parse_keywords(kw_raw, top_k=top_k)
        return summary, tags


class JsonBackend:
    """Backend that uses chat JSON output (one-shot) via OpenAI chat API.

    Requires a client exposing .chat(model=str, messages=list[dict], max_tokens=int, temperature=float=0.2) -> str
    which returns the assistant message content as a string.
    """

    def __init__(self, client: Any, *, model: str, max_tokens: int = 512, temperature: float = 0.2, prompts: Optional[PromptTexts] = None) -> None:
        if not isinstance(model, str) or not model:
            raise ValueError("model must be a non-empty string")
        if max_tokens <= 0:
            raise ValueError("max_tokens must be > 0")
        self._client = client
        self._model = model
        self._max_tokens = int(max_tokens)
        self._temperature = float(temperature)
        self._prompts = prompts or DEFAULT_PROMPTS

    def generate(self, text: str, *, top_k: int) -> Tuple[str, List[str]]:
        import json

        messages = build_json_messages(text, top_k, self._prompts)
        content = (self._client.chat(model=self._model, messages=messages, max_tokens=self._max_tokens, temperature=self._temperature) or "").strip()
        if not content:
            raise RuntimeError("LLM returned empty content for summary/tags")
        try:
            obj = json.loads(content)
        except Exception as e:
            raise RuntimeError("LLM returned non-JSON content: %s" % (e,))
        s = obj.get("summary")
        t = obj.get("tags")
        if not isinstance(s, str) or not s.strip():
            raise RuntimeError("LLM JSON missing or empty 'summary'")
        if not isinstance(t, list) or not t:
            raise RuntimeError("LLM JSON missing or empty 'tags'")
        tags = [str(x).strip() for x in t if str(x).strip()]
        if not tags:
            raise RuntimeError("LLM returned empty tags after normalization")
        return s.strip(), tags
