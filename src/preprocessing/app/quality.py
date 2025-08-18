from __future__ import annotations

from typing import Any, Dict

from ..domain.models import ParsedDocument
from ..domain.ports import QualityPort


class QualityService:
    """Evaluate document readability/quality with simple length thresholds.

    - Merges port-provided metrics with threshold evaluation results.
    - Adds keys: "ok": bool and "reasons": list[str].
    """

    def __init__(self, q: QualityPort, *, min_chars: int = 20, max_chars: int | None = None) -> None:
        if min_chars < 0:
            raise ValueError("min_chars must be >= 0")
        if max_chars is not None and max_chars < 0:
            raise ValueError("max_chars must be >= 0 when provided")
        self._q = q
        self._min = min_chars
        self._max = max_chars

    def evaluate(self, doc: ParsedDocument) -> Dict[str, Any]:
        metrics = self._q.evaluate(doc)  # arbitrary metrics from adapter
        text = doc.text or ""
        n = len(text)
        reasons: list[str] = []
        if n < self._min:
            reasons.append("too_short")
        if self._max is not None and n > self._max:
            reasons.append("too_long")
        ok = len(reasons) == 0

        result: Dict[str, Any] = dict(metrics)
        result["ok"] = ok
        result["reasons"] = reasons
        result["length"] = n
        result["min_chars"] = self._min
        result["max_chars"] = self._max
        return result

