import pytest

from src.preprocessing.adapters.translate import markdown_segmenter as mdseg


def test_round_trip_identity_preserves_structure(md_fixture):
    text = md_fixture("basic.md")
    segs, plan = mdseg.extract_segments(text, options={})
    # Identity translation map
    translated = [{"id": s.id, "text": s.text} for s in segs]
    out = mdseg.recombine(text, translated, plan, options={})
    assert out == text


def test_code_fences_and_inline_code_untouched():
    md = (
        "Before code\n\n"
        "```python\nprint('x')  \n```\n\n"
        "Inline `a+b` and link [lbl](https://example.com).\n"
    )
    segs, plan = mdseg.extract_segments(md, options={})
    # Ensure code fence content not extracted
    for s in segs:
        assert "print(" not in s.text
        assert "a+b" not in s.text  # inline code excluded
    out = mdseg.recombine(md, [{"id": s.id, "text": s.text} for s in segs], plan, options={})
    assert out == md


ess_table = (
    "| a | b |\n"
    "| --- | :---: |\n"
    "| c1 | c2 |\n"
)


def test_tables_cells_translated_but_layout_intact():
    segs, plan = mdseg.extract_segments(ess_table, options={})
    assert any(s.kind == "table_cell" for s in segs)
    # Replace cell texts deterministically
    repl = []
    for s in segs:
        if s.kind == "table_cell":
            repl.append({"id": s.id, "text": f"[{s.text}]"})
        else:
            repl.append({"id": s.id, "text": s.text})
    out = mdseg.recombine(ess_table, repl, plan, options={})
    # Pipes and alignment unchanged
    assert "| --- | :---: |" in out
    assert "[c1]" in out and "[c2]" in out


def test_long_paragraph_splitting_stable():
    txt = "This is a long paragraph. " * 50
    segs, _ = mdseg.extract_segments(txt, options={"segment_max_chars": 100})
    # Expect several parts with stable ordering and part indexes
    parts = [s for s in segs if s.part is not None]
    assert len(parts) > 1
    assert parts == sorted(parts, key=lambda s: s.order)


def test_id_mismatch_raises():
    text = "Hello world\n"
    _segs, plan = mdseg.extract_segments(text, options={})
    # Provide no translations -> mismatch
    with pytest.raises(mdseg.IdMismatchError):
        mdseg.recombine(text, [], plan, options={})
