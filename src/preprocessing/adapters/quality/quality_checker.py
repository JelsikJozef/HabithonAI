from __future__ import annotations

from typing import Any, Dict
import math
import re

from ...domain.models import ParsedDocument
from ...domain.ports import QualityPort


class QualityChecker(QualityPort):
    """Basic quality checks and simple metrics for text documents.

    - Thresholds: min_chars, optional max_chars, and min_words.
    - Stats include counts (chars, words, lines), avg_word_len, whitespace_ratio, reading_time_sec.
    - Returns a dict with keys: ok, reasons, stats.
    """

    def __init__(self, min_chars: int = 20, max_chars: int | None = None, min_words: int = 3) -> None:
        if min_chars < 0:
            raise ValueError("min_chars must be >= 0")
        if max_chars is not None and max_chars < 0:
            raise ValueError("max_chars must be >= 0 when provided")
        if min_words < 0:
            raise ValueError("min_words must be >= 0")
        self._min_chars = int(min_chars)
        self._max_chars = int(max_chars) if max_chars is not None else None
        self._min_words = int(min_words)

    def evaluate(self, doc: ParsedDocument) -> Dict[str, Any]:
        text = doc.text or ""
        # Basic counts
        chars = len(text)
        words_list = [w for w in re.findall(r"\w+", text, flags=re.UNICODE)]
        words = len(words_list)
        lines = text.count("\n") + (1 if text else 0)
        avg_word_len = (sum(len(w) for w in words_list) / words) if words else 0.0
        whitespace = sum(1 for c in text if c.isspace())
        whitespace_ratio = (whitespace / chars) if chars else 0.0
        reading_time_sec = int(math.ceil(words / (200 / 60))) if words else 0

        reasons = []
        if chars < self._min_chars:
            reasons.append("too_short")
        if self._max_chars is not None and chars > self._max_chars:
            reasons.append("too_long")
        if words < self._min_words:
            reasons.append("too_few_words")
        ok = len(reasons) == 0

        stats = {
            "chars": chars,
            "words": words,
            "lines": lines,
            "avg_word_len": avg_word_len,
            "whitespace_ratio": whitespace_ratio,
            "reading_time_sec": reading_time_sec,
            "min_chars": self._min_chars,
            "max_chars": self._max_chars,
            "min_words": self._min_words,
        }
        return {"ok": ok, "reasons": reasons, "stats": stats}
