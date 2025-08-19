from __future__ import annotations

import os
import json
from typing import Optional, Tuple, List

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:  # pragma: no cover - optional dep
    def load_dotenv(*args, **kwargs):  # type: ignore
        return False


class OpenAiClient:
    """Minimal OpenAI client wrapper for text completion and structured outputs.

    - Reads OPENAI_API_KEY from environment (.env loaded if present).
    - Exposes .complete(...) and .generate_summary_and_tags(text) -> (summary, [tags]).
    - Prompts are defined here (not in the enricher), tailored for Slovak output and token preservation.
    """

    def __init__(self, *, api_key: Optional[str] = None) -> None:
        load_dotenv()
        self._api_key = api_key or os.getenv("OPENAI_API_KEY")
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
            # Prefer listing models; it's lightweight and checks auth
            _ = self._client.models.list()
        except Exception:
            raise RuntimeError("Invalid or unauthorized OPENAI_API_KEY")

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
            return content or ""
        except Exception:
            try:
                resp = self._client.completions.create(
                    model=model,
                    prompt=prompt,
                    max_tokens=max_tokens,
                    temperature=0.2,
                )
                text = resp.choices[0].text  # type: ignore[attr-defined]
                return text or ""
            except Exception:
                return ""

    def generate_summary_and_tags(self, *, text: str, model: Optional[str] = None, max_tokens: int = 512) -> Tuple[str, List[str]]:
        """Return Slovak summary and tags in one request.

        Contract:
        - Input: raw or pseudonymized text; tokens like {{PII:TYPE:...}} must be preserved verbatim.
        - Output: (summary:str, tags:list[str]) with Slovak content.
        - Robust to non-JSON replies; attempts to parse gracefully.
        """
        mdl = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        system_msg = (
            "Si užitočný asistent. Z textu vytvor stručné, vecné zhrnutie v slovenčine a vyber zoznam relevantných kľúčových slov. "
            "Ak sa v texte nachádzajú špeciálne PII tokeny vo formáte {{PII:...}}, zachovaj ich presne bez zmien."
        )
        user_msg = (
            "VÝSTUP v JSON (bez ďalšieho textu): {\"summary\": \"...\", \"tags\": [\"...\", \"...\"]}.\n"
            "Požiadavky: zhrnutie 1–3 vety v slovenčine; tags je zoznam 3–10 krátkych slovenských výrazov; bez diakritiky nemusia byť odstránené; nezavádzaj ďalší text.\n\n"
            "Text:\n" + text
        )
        try:
            resp = self._client.chat.completions.create(
                model=mdl,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                max_tokens=max_tokens,
                temperature=0.2,
            )
            content = getattr(resp.choices[0].message, "content", "") or ""
        except Exception:
            content = ""
        summary = ""  # type: str
        tags = []      # type: List[str]
        # Try strict JSON parse first
        try:
            obj = json.loads(content)
            s = obj.get("summary")
            t = obj.get("tags")
            if isinstance(s, str):
                summary = s.strip()
            if isinstance(t, list):
                tags = [str(x).strip() for x in t if str(x).strip()]
        except Exception:
            # Fallback: try to split lines and infer
            if content:
                lines = [l.strip() for l in content.splitlines() if l.strip()]
                if lines:
                    summary = lines[0]
                    # Remaining lines or comma parts as tags
                    rest = ", ".join(lines[1:]) if len(lines) > 1 else ""
                    raw = [p for ln in ([rest] if rest else []) for p in ln.split(",")]
                    tags = [p.strip() for p in raw if p.strip()]
        return summary, tags
