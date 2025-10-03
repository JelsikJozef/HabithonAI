from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:  # pragma: no cover - soft optional

    def load_dotenv() -> None:  # type: ignore
        return None


# Load .env from repo root early (idempotent)
load_dotenv()


@dataclass
class LLMResult:
    summary: str
    keywords: List[str]
    usage: Dict[str, Any]


class OpenAIClientError(RuntimeError):
    pass


def _get_api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY")
    if not key or not key.strip():
        raise OpenAIClientError(
            "OPENAI_API_KEY is not set. Create a .env file with OPENAI_API_KEY=... or export it in the environment."
        )
    return key.strip()


def _build_client():
    # Deferred import to keep tests lightweight when mocked
    try:
        from openai import OpenAI
    except Exception as e:  # pragma: no cover - exercised only if dependency missing
        raise OpenAIClientError(
            "The 'openai' package is required but not installed. Add openai>=1.42.0 to requirements and install."
        ) from e

    key = _get_api_key()
    # The OpenAI client reads key from environment or parameter
    os.environ.setdefault("OPENAI_API_KEY", key)
    return OpenAI()


def _clean_keywords(items: List[str]) -> List[str]:
    cleaned: List[str] = []
    seen = set()
    for it in items:
        if not isinstance(it, str):
            continue
        s = it.strip().lower()
        # allow a-z 0-9 space and hyphen only
        s = "".join(ch for ch in s if ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch in {" ", "-"})
        s = " ".join(part for part in s.split() if part)
        if not s or s in seen:
            continue
        seen.add(s)
        cleaned.append(s)
    return cleaned


def _omit_sampling_params(model: str) -> bool:
    """Return True if we must not pass temperature/top_p/seed for this model.

    Per OpenAI docs, some GPT-5 class models do not accept temperature!=default (and may reject the field entirely).
    We conservatively omit sampling params when model starts with 'gpt-5'.
    """
    m = (model or "").strip().lower()
    return m.startswith("gpt-5")


def summarize_keywords(
    text: str,
    model: str = "gpt-4o-mini",
    timeout_s: int = 60,
    seed: Optional[int] = 0,
) -> Dict[str, Any]:
    """
    Call OpenAI to summarize and extract exactly 5 keywords from anonymized text.

    Returns dict: {"summary": str, "keywords": [str*5], "usage": {...}}
    Deterministic settings for GPT-4 era: temperature=0, top_p=1, seed=seed (default 0).
    Implements basic retry on 429/5xx with exponential backoff.
    """
    if not isinstance(text, str):
        raise OpenAIClientError("text must be a string")

    client = _build_client()

    system_prompt = "You are a precise assistant. Return only strict JSON. No extra text."
    user_prompt = (
        "Task: From the anonymized English document, return:\n"
        "- summary: exactly one sentence, <= 30 words, plain English.\n"
        "- keywords: exactly 5 items, lowercase, ASCII letters/digits-hyphen/space only, deduped, sorted by importance.\n\n"
        "Output schema:\n"
        '{"summary":"<one sentence>", "keywords":["kw1","kw2","kw3","kw4","kw5"]}\n\n'
        "Document:\n<BEGIN_DOCUMENT>\n"
        f"{text}\n"
        "<END_DOCUMENT>"
    )

    max_attempts = 4
    base_sleep = 1.0
    last_err: Optional[Exception] = None

    # Use deterministic random jitter seeded if provided
    rng = random.Random(seed if seed is not None else 0)

    omit_sampling = _omit_sampling_params(model)

    for attempt in range(1, max_attempts + 1):
        try:
            # Prefer Responses API if available; fall back to Chat Completions.
            try:
                # type: ignore[attr-defined]
                kwargs: Dict[str, Any] = {
                    "model": model,
                    "input": [
                        {
                            "role": "system",
                            "content": system_prompt,
                        },
                        {
                            "role": "user",
                            "content": user_prompt,
                        },
                    ],
                    "timeout": timeout_s,
                    "response_format": {"type": "json_object"},
                }
                if not omit_sampling:
                    kwargs.update(
                        {
                            "temperature": 0,
                            "top_p": 1,
                            "seed": seed if seed is not None else 0,
                        }
                    )
                response = client.responses.create(**kwargs)
                # Map Responses API to text content
                content_text = None
                try:
                    # Newer SDK: response.output_text
                    content_text = getattr(response, "output_text", None)
                except Exception:
                    content_text = None
                if not content_text:
                    # Fallback: dig into output choices
                    try:
                        content_text = response.output[0].content[0].text
                    except Exception:
                        content_text = None
                if not content_text:
                    raise OpenAIClientError("Empty response from model")
                raw = content_text
                usage = getattr(response, "usage", None)
            except Exception:
                # Fall back to Chat Completions
                kwargs_c: Dict[str, Any] = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "timeout": timeout_s,
                    "response_format": {"type": "json_object"},
                }
                if not omit_sampling:
                    kwargs_c.update(
                        {
                            "temperature": 0,
                            "top_p": 1,
                            "seed": seed if seed is not None else 0,
                        }
                    )
                chat = client.chat.completions.create(**kwargs_c)
                msg = chat.choices[0].message
                raw = msg.content or ""
                usage = getattr(chat, "usage", None)

            data = json.loads(raw)
            summary = str(data.get("summary", "")).strip()
            kw = data.get("keywords", [])
            if not isinstance(kw, list):
                kw = []
            keywords = _clean_keywords([str(x) for x in kw])
            # Enforce exactly 5 by padding/truncating (caller will validate anyway)
            keywords = keywords[:5]
            while len(keywords) < 5:
                keywords.append("")
            return {
                "summary": summary,
                "keywords": keywords,
                "usage": usage or {},
            }
        except Exception as e:  # retry on transient errors
            last_err = e
            # Heuristic: retry for HTTP 429/5xx or generic APIError messages
            msg = str(e)
            transient = any(
                x in msg for x in ["429", "rate limit", "temporar", "timeout", "5", "unavailable"]
            )  # noqa: E501
            if attempt >= max_attempts or not transient:
                break
            sleep_s = base_sleep * (2 ** (attempt - 1))
            # bounded jitter +/- 20%
            jitter = 0.2 * sleep_s
            time.sleep(max(0.1, sleep_s + rng.uniform(-jitter, jitter)))

    raise OpenAIClientError(f"OpenAI call failed after {max_attempts} attempts: {last_err}")


def get_available_gpt5_models() -> List[str]:
    """Return a list of available GPT-5 model names.

    Attempts to query the OpenAI client for available models and filters those
    that include 'gpt-5'. If the SDK or network call is unavailable, returns
    a conservative curated fallback list so the GUI can present options.
    """
    candidates: List[str] = []
    # Curated fallback list (kept reasonably small and safe)
    fallback = [
        "gpt-5-mini",
        "gpt-5",
        "gpt-5-nano",
        "gpt-5-chat-latest",
        "gpt-5-mini-2025-08-07",
        "gpt-5-nano-2025-08-07",
    ]
    try:
        client = _build_client()
        try:
            # Newer SDK exposes models.list(); adapt to available shapes
            models_res = getattr(client, "models", None)
            if models_res is None:
                return fallback
            # Attempt to call .list() if present
            list_fn = getattr(models_res, "list", None)
            if callable(list_fn):
                resp = list_fn()
                # resp.data may be list-like of objects with 'id' attribute
                data = getattr(resp, "data", None) or resp
                for m in data:
                    try:
                        mid = getattr(m, "id", None) or (
                            m.get("id") if isinstance(m, dict) else None
                        )
                        if isinstance(mid, str) and "gpt-5" in mid:
                            candidates.append(mid)
                    except Exception:
                        continue
            else:
                # If models is an iterable, try iterating
                try:
                    for m in models_res:
                        mid = getattr(m, "id", None) or (
                            m.get("id") if isinstance(m, dict) else None
                        )
                        if isinstance(mid, str) and "gpt-5" in mid:
                            candidates.append(mid)
                except Exception:
                    pass
        except Exception:
            # Query failed; return fallback
            return fallback
    except Exception:
        # Building client failed (missing API key or package); return fallback
        return fallback

    # If query returned nothing, use fallback
    if not candidates:
        return fallback
    # Deduplicate and sort
    seen = set()
    out: List[str] = []
    for m in candidates:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out
