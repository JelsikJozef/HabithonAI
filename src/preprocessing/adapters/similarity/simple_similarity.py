from __future__ import annotations

"""
Simple deterministic similarity scorer for strings.

Implements SimilarityPort using character trigram Jaccard similarity. The score
is in [0,1], with 1 meaning identical. This is model-free but deterministic and
sufficient for detecting near-identity translations (no-op).
"""

from preprocessing.domain.ports import SimilarityPort


def _trigrams(s: str) -> set[str]:
    s = (s or "").strip()
    if not s:
        return set()
    # Normalize whitespace collapse to reduce noise
    s = " ".join(s.split())
    if len(s) < 3:
        return {s}
    return {s[i : i + 3] for i in range(len(s) - 2)}


class TrigramJaccardSimilarity(SimilarityPort):
    def similarity(self, a: str, b: str) -> float:  # type: ignore[override]
        A = _trigrams(a)
        B = _trigrams(b)
        if not A and not B:
            return 1.0
        if not A or not B:
            return 0.0
        inter = len(A & B)
        union = len(A | B)
        if union == 0:
            return 0.0
        return inter / union


__all__ = ["TrigramJaccardSimilarity"]
