"""Regression tests for ``merge_overlapping_entities``.

CLAUDE.md requires detection to yield a **pairwise non-overlapping** entity set
(priority: PERSON > ORGANIZATION, then longer span, then higher score). The
deterministic anonymize -> deanonymize round-trip (sequential ``str.replace``)
depends on this invariant. These tests pin the email case that originally leaked
plus the two adjacent paths of the same invariant.
"""

from pathlib import Path

from anonymization.adapters.crypto.crypto import Crypto
from anonymization.adapters.token_vault.file_store import FileTokenVault
from anonymization.app.denomize import deanonymize
from anonymization.domain.anonymizer import anonymize
from anonymization.domain.entities import (
    PiiEntity,
    merge_overlapping_entities,
    spans_overlap,
)


def assert_no_pairwise_overlap(result: list[PiiEntity]) -> None:
    """No two spans in the merged result may overlap (the CLAUDE.md invariant)."""
    for i in range(len(result)):
        for j in range(i + 1, len(result)):
            a, b = result[i], result[j]
            assert not spans_overlap(a.start, a.end, b.start, b.end), f"overlap between {a} and {b}"


# --------------------------------------------------------------------------- email
EMAIL_TEXT = "Contact john.doe@example.com now."
_EMAIL = "john.doe@example.com"
_E_START = EMAIL_TEXT.index(_EMAIL)
_E_END = _E_START + len(_EMAIL)


def _email_entities() -> list[PiiEntity]:
    """The Presidio-style emission: full EMAIL_ADDRESS + two partial URL spans."""
    dom_start = EMAIL_TEXT.index("example.com")
    return [
        PiiEntity("EMAIL_ADDRESS", _E_START, _E_END, _EMAIL, score=1.0, detector="presidio"),
        PiiEntity("URL", dom_start, _E_END, "example.com", score=0.5, detector="presidio"),
        PiiEntity("URL", _E_START, _E_START + 8, "john.doe", score=0.5, detector="presidio"),
    ]


class _ListDetector:
    """Detector that returns a fixed entity list regardless of input."""

    name = "list"

    def __init__(self, entities: list[PiiEntity]) -> None:
        self._entities = entities

    def detect(self, text: str, language: str | None = None) -> list[PiiEntity]:
        return list(self._entities)


def test_email_with_partial_urls_merges_to_single_span() -> None:
    merged = merge_overlapping_entities(_email_entities())
    assert len(merged) == 1
    only = merged[0]
    assert only.type == "EMAIL_ADDRESS"
    assert (only.start, only.end) == (_E_START, _E_END)
    assert_no_pairwise_overlap(merged)


def test_email_round_trip(tmp_path: Path) -> None:
    vault = FileTokenVault(base_dir=str(tmp_path / "vault"))
    ctx = "email-ctx"
    res = anonymize(
        EMAIL_TEXT,
        detectors=[_ListDetector(_email_entities())],
        crypto=Crypto(),
        vault=vault,
        context_id=ctx,
    )
    # The full email must be hidden, not just fragments of it.
    assert _EMAIL not in res.pseudonymized_text
    assert len(res.mappings) == 1
    back = deanonymize(res.pseudonymized_text, vault, context_id=ctx)
    assert back.restored_text == EMAIL_TEXT


# ----------------------------------------------------------- one span over two kept
def test_one_span_over_two_kept_drops_both() -> None:
    # Equal priority: a wide span covering two shorter disjoint spans. Before the
    # fix the missing equal-priority branch appended A and B alongside W -> overlap.
    entities = [
        PiiEntity("OTHER", 0, 5, "AAAAA", score=0.9),
        PiiEntity("OTHER", 10, 15, "BBBBB", score=0.9),
        PiiEntity("OTHER", 0, 15, "AAAAA-----BBBBB", score=0.5),
    ]
    merged = merge_overlapping_entities(entities)
    assert len(merged) == 1
    assert (merged[0].start, merged[0].end) == (0, 15)
    assert_no_pairwise_overlap(merged)


# ----------------------------------------------------------------- cross-priority
def test_cross_priority_person_wins_over_organization() -> None:
    entities = [
        PiiEntity("ORGANIZATION", 0, 20, "Acme Corporation Ltd", score=0.9),
        PiiEntity("PERSON", 5, 12, "Corpora", score=0.6),
    ]
    merged = merge_overlapping_entities(entities)
    assert len(merged) == 1
    assert merged[0].type == "PERSON"
    assert_no_pairwise_overlap(merged)


def test_mixed_cluster_is_pairwise_non_overlapping() -> None:
    # cross-priority + equal-priority overlaps together; only the invariant matters.
    entities = [
        PiiEntity("ORGANIZATION", 0, 20, "org-span", score=0.9),
        PiiEntity("PERSON", 5, 12, "person", score=0.6),
        PiiEntity("EMAIL_ADDRESS", 18, 40, "a@b.com", score=1.0),
        PiiEntity("URL", 25, 35, "b.com", score=0.5),
        PiiEntity("PHONE", 50, 60, "1234567890", score=0.8),
    ]
    merged = merge_overlapping_entities(entities)
    assert_no_pairwise_overlap(merged)
    # the disjoint PHONE span must survive
    assert any(e.type == "PHONE" for e in merged)


def test_merge_is_deterministic_under_input_shuffle() -> None:
    entities = _email_entities() + [
        PiiEntity("PHONE", 50, 60, "1234567890", score=0.8),
        PiiEntity("PERSON", 40, 48, "John Doe", score=0.6),
    ]
    a = merge_overlapping_entities(entities)
    b = merge_overlapping_entities(list(reversed(entities)))
    assert [(e.start, e.end, e.type) for e in a] == [(e.start, e.end, e.type) for e in b]
