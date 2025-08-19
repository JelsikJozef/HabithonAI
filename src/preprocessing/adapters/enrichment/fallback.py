from __future__ import annotations

"""Heuristic fallbacks for summary and keywords.

These are not used in fail-fast processing but are kept to avoid duplication
if heuristics are reintroduced for non-critical tasks (e.g., UI previews).
"""

from typing import List
import re


def summarize_fallback(text: str, max_sentences: int = 2) -> str:
    if not text:
        return ""
    # Naive sentence split by punctuation. Keep short first sentences.
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    sentences = [p.strip() for p in parts if p.strip()]
    return " ".join(sentences[: max(1, max_sentences)])


def keywords_fallback(text: str, top_k: int = 10) -> List[str]:
    if not text:
        return []
    # Very naive: split on non-word boundaries, filter stopwords, dedupe, keep order
    stop = {
        "a","i","s","sa","si","som","sme","ste","je","su","by","aby","ktorý","ktorá","ktoré",
        "na","v","vo","z","zo","do","od","u","pri","pre","pod","nad","popri","po","za","bez",
        "ten","tá","to","toto","tamtie","tento","táto","tieto","ktoré","ktorí",
    }
    tokens = re.split(r"[^\w]+", text, flags=re.UNICODE)
    seen = set()
    out: List[str] = []
    for t in tokens:
        s = t.strip().strip("-•* _")
        if not s:
            continue
        low = s.lower()
        if len(low) < 3 or low in stop:
            continue
        if low in seen:
            continue
        seen.add(low)
        out.append(s)
        if len(out) >= max(1, top_k):
            break
    return out
