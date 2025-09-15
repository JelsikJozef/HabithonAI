from __future__ import annotations

from src.preprocessing.adapters.similarity.simple_similarity import TrigramJaccardSimilarity


def test_similarity_identity_and_empty():
    sim = TrigramJaccardSimilarity()
    assert sim.similarity("hello", "hello") == 1.0
    assert sim.similarity("", "") == 1.0
    assert sim.similarity("", "a") == 0.0


def test_similarity_whitespace_normalization():
    sim = TrigramJaccardSimilarity()
    a = "Hello   world"
    b = "Hello world"
    # Should normalize internal whitespace equally
    s = sim.similarity(a, b)
    assert s == 1.0


def test_similarity_partial_overlap():
    sim = TrigramJaccardSimilarity()
    a = "abcdefg"
    b = "abcxyzg"
    s = sim.similarity(a, b)
    assert 0.0 < s < 1.0


def test_similarity_disjoint():
    sim = TrigramJaccardSimilarity()
    assert sim.similarity("abc", "xyz") == 0.0
