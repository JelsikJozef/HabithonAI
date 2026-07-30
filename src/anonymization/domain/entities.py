from dataclasses import dataclass


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
    score: float | None = None
    detector: str | None = None


@dataclass
class DetectionResult:
    text: str
    entities: list[PiiEntity]


@dataclass(frozen=True)
class TokenMapping:
    token: str
    value: str
    type: str


@dataclass
class PseudonymizationResult:
    original_text: str
    pseudonymized_text: str
    mappings: list[TokenMapping]


@dataclass
class DeAnonymizationResult:
    anonymized_text: str
    restored_text: str
    mappings_used: list[TokenMapping]


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


def merge_overlapping_entities(entities: list[PiiEntity]) -> list[PiiEntity]:
    """Return a **pairwise non-overlapping** set of entities.

    Overlap resolution (the winner takes the span, the losers are dropped):
    higher type priority (PERSON > ORGANIZATION > other), then the longer span,
    then the higher score; remaining ties are broken deterministically by start,
    end, type, value and detector so the same input always yields the same set.

    The output is guaranteed to contain no overlapping pair for *any* input and
    *any* detector ordering. The deterministic anonymize -> deanonymize round-trip
    (sequential ``str.replace`` over the token mappings) depends on this invariant.
    """
    if not entities:
        return []
    # Winner-first total order: process the highest-priority / longest / highest-score
    # span of each overlapping cluster first, then accept a span only if it does not
    # overlap anything already kept. The trailing keys make the order total (no ties),
    # so the merged set is fully deterministic.
    ordered = sorted(
        entities,
        key=lambda e: (
            -_type_priority(e.type),
            -(e.end - e.start),
            -(e.score if e.score is not None else 0.0),
            e.start,
            e.end,
            e.type,
            e.value,
            e.detector or "",
        ),
    )
    kept: list[PiiEntity] = []
    for ent in ordered:
        if any(spans_overlap(ent.start, ent.end, k.start, k.end) for k in kept):
            continue  # a higher-ranked span already owns this region
        kept.append(ent)
    return sorted(kept, key=lambda e: (e.start, e.end))
