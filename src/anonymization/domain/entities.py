from dataclasses import dataclass
from typing import List, Dict, Optional


@dataclass(frozen=True)
class PiiEntity:
    """Represents a detected PII span in text.

    - type: a short label like EMAIL, PHONE, CREDIT_CARD, IP, NAME, etc.
    - start: start char offset (inclusive)
    - end: end char offset (exclusive)
    - value: the raw substring
    - score: optional confidence score in [0,1]
    - detector: which detector produced this entity
    """
    type: str
    start: int
    end: int
    value: str
    score: Optional[float] = None
    detector: Optional[str] = None


@dataclass
class DetectionResult:
    text: str
    entities: List[PiiEntity]


@dataclass(frozen=True)
class TokenMapping:
    token: str
    value: str
    type: str


@dataclass
class PseudonymizationResult:
    original_text: str
    pseudonymized_text: str
    mappings: List[TokenMapping]


@dataclass
class DeAnonymizationResult:
    anonymized_text: str
    restored_text: str
    mappings_used: List[TokenMapping]


def spans_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return not (a_end <= b_start or b_end <= a_start)


def merge_overlapping_entities(entities: List[PiiEntity]) -> List[PiiEntity]:
    """Return a list of non-overlapping entities.

    If overlaps occur, keep the longer span; if equal length, keep the higher score; if tie, keep the first.
    """
    if not entities:
        return []
    # Sort by start, then by -length, then by score desc
    entities_sorted = sorted(
        entities,
        key=lambda e: (e.start, -(e.end - e.start), -(e.score if e.score is not None else 0.0)),
    )
    kept: List[PiiEntity] = []
    for ent in entities_sorted:
        conflict = False
        for k in kept:
            if spans_overlap(ent.start, ent.end, k.start, k.end):
                conflict = True
                # Decide which to keep
                ent_len = ent.end - ent.start
                k_len = k.end - k.start
                if ent_len > k_len:
                    # replace k
                    kept.remove(k)
                    kept.append(ent)
                elif ent_len == k_len:
                    ent_score = ent.score or 0.0
                    k_score = k.score or 0.0
                    if ent_score > k_score:
                        kept.remove(k)
                        kept.append(ent)
                break
        if not conflict:
            kept.append(ent)
    # Re-sort by start position
    return sorted(kept, key=lambda e: e.start)

