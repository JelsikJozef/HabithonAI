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


def _type_priority(t: str) -> int:
    """Priority for resolving overlaps: higher wins.

    - PERSON should not be swallowed by broader spans
    - ORGANIZATION next
    - others default to 0
    """
    if t == "PERSON":
        return 2
    if t == "ORGANIZATION":
        return 1
    return 0


def merge_overlapping_entities(entities: List[PiiEntity]) -> List[PiiEntity]:
    """Return a list of non-overlapping entities.

    Overlap resolution prefers higher-priority types (e.g., PERSON over ORGANIZATION).
    If equal priority, keep the longer span; if equal length, keep the higher score; if tie, keep the first seen.
    """
    if not entities:
        return []
    # Sort by start, then by type priority desc, then by -length, then by score desc
    entities_sorted = sorted(
        entities,
        key=lambda e: (
            e.start,
            -_type_priority(e.type),
            -(e.end - e.start),
            -(e.score if e.score is not None else 0.0),
        ),
    )
    kept: List[PiiEntity] = []
    for ent in entities_sorted:
        replaced = False
        for i, k in enumerate(list(kept)):
            if spans_overlap(ent.start, ent.end, k.start, k.end):
                ent_pri = _type_priority(ent.type)
                k_pri = _type_priority(k.type)
                if ent_pri > k_pri:
                    kept[i] = ent
                    replaced = True
                    break
                elif ent_pri < k_pri:
                    replaced = True  # ent loses; skip adding
                    break
                else:
                    # equal priority: prefer longer, then higher score
                    ent_len = ent.end - ent.start
                    k_len = k.end - k.start
                    if ent_len > k_len:
                        kept[i] = ent
                        replaced = True
                        break
                    elif ent_len == k_len:
                        ent_score = ent.score or 0.0
                        k_score = k.score or 0.0
                        if ent_score > k_score:
                            kept[i] = ent
                            replaced = True
                            break
                        else:
                            replaced = True  # keep existing
                            break
        if not replaced:
            kept.append(ent)
    # Re-sort by start position
    return sorted(kept, key=lambda e: e.start)
