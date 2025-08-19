from __future__ import annotations

import json
from typing import Optional, Tuple, List, Sequence


class OpenAiClient:
    """Minimal OpenAI client wrapper for text completion and structured outputs.

    - Requires API key provided explicitly via constructor.
    - Exposes .complete(...), .chat(...), and .generate_summary_and_tags(...).
    - Prompts are centralized elsewhere; generate_summary_and_tags delegates to them.
    - Fail-fast on errors: any API failure, empty, or invalid output raises.
    """

    def __init__(self, *, api_key: Optional[str] = None) -> None:
        self._api_key = api_key
        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY is required but not set")
        # Lazy import to avoid hard dependency in environments without openai
        try:
            from openai import OpenAI  # type: ignore
        except Exception:  # pragma: no cover - only executes when missing
            raise RuntimeError("openai package is not installed")
        self._client = OpenAI(api_key=self._api_key)
        # Validate key early with a minimal authenticated call
        try:
            _ = self._client.models.list()
        except Exception as e:
            raise RuntimeError("Invalid or unauthorized OPENAI_API_KEY: %s" % (e,))

    def complete(self, *, prompt: str, model: str, max_tokens: int = 512) -> str:
        try:
            resp = self._client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.2,
            )
            choice = resp.choices[0]
            content = getattr(choice.message, "content", None)
            out = (content or "").strip()
            if not out:
                raise RuntimeError("OpenAI returned empty completion content")
            return out
        except Exception as e:
            raise RuntimeError("OpenAI completion failed: %s" % (e,))

    def chat(self, *, model: str, messages: Sequence[dict], max_tokens: int = 512, temperature: float = 0.2) -> str:
        """Generic chat call returning the assistant content string."""
        try:
            resp = self._client.chat.completions.create(
                model=model,
                messages=list(messages),
                max_tokens=max_tokens,
                temperature=temperature,
            )
            content = getattr(resp.choices[0].message, "content", "") or ""
            out = content.strip()
            if not out:
                raise RuntimeError("OpenAI returned empty chat content")
            return out
        except Exception as e:
            raise RuntimeError("OpenAI chat call failed: %s" % (e,))

    def generate_summary_and_tags(self, *, text: str, model: Optional[str] = None, max_tokens: int = 512, top_k: int = 10) -> Tuple[str, List[str]]:
        """Return Slovak summary and tags in one request using centralized prompts.

        Contract:
        - Input: raw or pseudonymized text; tokens like {{PII:TYPE:...}} must be preserved verbatim.
        - Output: (summary:str, tags:list[str]) with Slovak content.
        - Strict: response must be JSON with required fields; invalid outputs raise.
        """
        from .prompts_sk import build_json_messages  # local import to avoid cycles

        mdl = model or "gpt-4o-mini"
        messages = build_json_messages(text, top_k)
        content = self.chat(model=mdl, messages=messages, max_tokens=max_tokens, temperature=0.2)
        obj = json.loads(content)
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
