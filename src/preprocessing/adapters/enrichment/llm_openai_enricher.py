from __future__ import annotations

from typing import Optional, List, Tuple
import logging
import re
from collections import Counter

from .openai_client import OpenAiClient
from ...domain.ports import LlmEnrichmentPort

logger = logging.getLogger(__name__)


class OpenAiLlmEnricher(LlmEnrichmentPort):
    """LLM enricher that calls OpenAI once per input to get summary and tags.

    - No prompt logic here; prompts live in OpenAiClient.generate_summary_and_tags.
    - Caches last result keyed by the exact text string to avoid double calls
      when summarize() and keywords() are invoked separately on the same text.
    - Falls back to a local heuristic summarizer/keyword extractor if the client returns empty.
    """

    def __init__(self, client: OpenAiClient, *, model: Optional[str] = None, max_tokens: int = 512) -> None:
        self._client = client
        self._model = model
        self._max_tokens = int(max_tokens)
        self._cache_key: Optional[str] = None
        self._cache_value: Optional[Tuple[str, List[str]]] = None

    def _fallback(self, text: str) -> Tuple[str, List[str]]:
        s = self._fallback_summary(text)
        tags = self._fallback_tags(text)
        logger.info("LLM fallback used (local heuristics)")
        return s, tags

    def _fallback_summary(self, text: str) -> str:
        s = (text or "").strip()
        if not s:
            return ""
        # Split into sentences by period, exclam, question; keep first 1–3 sentences under ~400 chars
        parts = re.split(r"(?<=[.!?])\s+", s)
        out = []
        total = 0
        for p in parts:
            if not p:
                continue
            out.append(p)
            total += len(p)
            if len(out) >= 3 or total > 400:
                break
        return " ".join(out)

    def _fallback_tags(self, text: str, k: int = 10) -> List[str]:
        s = (text or "").lower()
        # crude tokenization: basic Latin + Latin-1 Supplement + Latin Extended-A/B ranges and digits
        toks = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿĀ-ž0-9]{3,}", s)
        # stoplist (sk + generic)
        stop = {
            "a","aj","ale","ani","ako","aby","bol","bola","boli","by","do","je","iba","ich","ja","ju","k","každý","keď","kde","ktorý","ktorá","ktoré","ktorí","ktor","lebo","len","ma","má","mal","mali","mi","môže","musia","na","nad","naj","ne","nebo","nie","niečo","no","od","po","pod","pri","pre","sa","s","so","spolu","sú","tak","takže","tam","ten","tá","to","toto","tu","už","v","vo","z","za","že","www","http","https","com","sk","cz","de","at","eu","profidecon"
        }
        words = [w for w in toks if w not in stop]
        freq = Counter(words)
        return [w for w, _ in freq.most_common(k)]

    def _ensure(self, text: str) -> Tuple[str, List[str]]:
        if self._cache_key == text and self._cache_value is not None:
            return self._cache_value
        try:
            summary, tags = self._client.generate_summary_and_tags(text=text, model=self._model, max_tokens=self._max_tokens)
        except Exception as e:
            logger.warning("OpenAI call failed: %s", e)
            summary, tags = "", []
        if (not summary) and (not tags):
            summary, tags = self._fallback(text)
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
