Some intro.\n\n```js\nfunction x() { return 1; }\n```\n\nMore text after.\n

from pathlib import Path
from typing import Dict

from preprocessing.services.segmenter.api import SegmenterOptions, extract_segments, recombine


def read_golden(name: str) -> str:
    base = Path(__file__).parent / "golden" / name
    return base.read_text(encoding="utf-8")


def test_identity_simple():
    md = read_golden("sample_simple.md")
    segs, plan = extract_segments(md, SegmenterOptions())
    mapping: Dict[str, str] = {s.id: s.text for s in segs}
    out = recombine(md, mapping, plan)
    assert out == md


def test_identity_tables():
    md = read_golden("sample_tables.md")
    segs, plan = extract_segments(md, SegmenterOptions())
    mapping: Dict[str, str] = {s.id: s.text for s in segs}
    out = recombine(md, mapping, plan)
    assert out == md


def test_identity_links():
    md = read_golden("sample_links.md")
    segs, plan = extract_segments(md, SegmenterOptions())
    mapping: Dict[str, str] = {s.id: s.text for s in segs}
    out = recombine(md, mapping, plan)
    assert out == md


def test_identity_codeblocks():
    md = read_golden("sample_codeblocks.md")
    segs, plan = extract_segments(md, SegmenterOptions())
    mapping: Dict[str, str] = {s.id: s.text for s in segs}
    out = recombine(md, mapping, plan)
    assert out == md
