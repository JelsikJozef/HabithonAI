from __future__ import annotations

from typing import Dict

from preprocessing.services.segmenter.api import SegmenterOptions, extract_segments, recombine


def test_no_code_segments():
    md = "Here is `inline code` and below is a fence:\n\n" "```python\nprint('hi')\n```\n"
    segs, _ = extract_segments(md, SegmenterOptions())
    texts = [s.text for s in segs]
    assert any("Here is" in t for t in texts)
    assert not any("inline code" in t for t in texts)
    assert not any("print('hi')" in t for t in texts)


essay = "This is a paragraph with a link to [OpenAI](https://openai.com) and an image ![Alt Text](img.png).\n"


def test_link_url_not_translated_and_labels_toggle():
    # labels on
    segs_on, plan_on = extract_segments(essay, SegmenterOptions(translate_link_labels=True))
    labels = [
        s.text
        for s in segs_on
        if s.kind in {"paragraph", "heading", "list_item", "blockquote", "table_cell"}
    ]
    assert any("This is a paragraph" in t for t in labels)
    assert any("OpenAI" == t or "Alt Text" == t for t in segs_on)

    # labels off
    segs_off, plan_off = extract_segments(essay, SegmenterOptions(translate_link_labels=False))
    texts_off = [s.text for s in segs_off]
    assert not any(t == "OpenAI" for t in texts_off)


def test_tables_toggle_and_splitting():
    md = "| H1 | H2 |\n\n" "| --- | --- |\n" "| A | B |\n" "| C | D |\n"
    segs_on, _ = extract_segments(md, SegmenterOptions(translate_tables=True))
    assert any(s.kind == "table_cell" for s in segs_on)
    segs_off, _ = extract_segments(md, SegmenterOptions(translate_tables=False))
    assert not any(s.kind == "table_cell" for s in segs_off)

    # Splitting: long paragraph
    long = "word " * 1200
    segs, _ = extract_segments(long, SegmenterOptions(max_segment_chars=100))
    assert all((s.end - s.start) <= 100 for s in segs)
