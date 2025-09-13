from __future__ import annotations

"""Public Segmenter API: extract Markdown segments and recombine.

This module provides a stable, small API surface used by translation adapters.
It orchestrates block scanning and inline extraction, producing Segment objects
and a SegmentPlan suitable for lossless recombination.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Mapping, Tuple

from .blocks import BlockScanner
from .inlines import extract_inlines_from_block
from .plan import assert_non_overlapping, make_plan_components

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Segment:
    id: str
    start: int
    end: int
    text: str
    kind: str  # e.g. "paragraph", "heading", "list", "table_cell", "link_label", "inline_text"


@dataclass(frozen=True)
class SegmentPlan:
    spans: Dict[str, Tuple[int, int]]
    ordered_ids: Tuple[str, ...]


@dataclass(frozen=True)
class SegmenterOptions:
    max_segment_chars: int = 2000
    translate_tables: bool = True
    translate_link_labels: bool = True


def extract_segments(md_text: str, options: SegmenterOptions) -> Tuple[List[Segment], SegmentPlan]:
    """Parse Markdown and return translatable segments and a stable recombination plan."""
    if not isinstance(md_text, str):
        raise TypeError("md_text must be a string")

    segments: List[Segment] = []
    span_items: List[Tuple[str, Tuple[int, int]]] = []

    order_counter = 0
    scanner = BlockScanner(md_text)

    for block in scanner.scan_blocks():
        if block.kind in {"code_fence", "code_indented", "html_block"}:
            continue

        if block.kind == "table" and not options.translate_tables:
            continue

        if block.kind == "table":
            for cell in scanner.iter_table_cells(block):
                for seg_id, span, text, kind in extract_inlines_from_block(
                    md_text=md_text,
                    block=cell,
                    kind="table_cell",
                    order_start=order_counter,
                    translate_labels=options.translate_link_labels,
                    translate_alts=True,
                    max_chars=options.max_segment_chars,
                ):
                    segments.append(
                        Segment(id=seg_id, start=span[0], end=span[1], text=text, kind=kind)
                    )
                    span_items.append((seg_id, span))
                    order_counter += 1
        else:
            kind = "heading" if block.kind == "heading" else block.kind
            for seg_id, span, text, _ in extract_inlines_from_block(
                md_text=md_text,
                block=block,
                kind=kind,
                order_start=order_counter,
                translate_labels=options.translate_link_labels,
                translate_alts=True,
                max_chars=options.max_segment_chars,
            ):
                segments.append(
                    Segment(id=seg_id, start=span[0], end=span[1], text=text, kind=kind)
                )
                span_items.append((seg_id, span))
                order_counter += 1

    ordered = sorted(span_items, key=lambda p: (p[1][0], p[1][1]))
    assert_non_overlapping([s for _, s in ordered])

    spans, ordered_ids = make_plan_components(ordered)
    plan = SegmentPlan(spans=spans, ordered_ids=ordered_ids)
    return segments, plan


def recombine(
    md_text: str,
    translated: Mapping[str, str],
    plan: SegmentPlan,
) -> str:
    """Recompose the Markdown from the original text and per-segment translations."""
    from .recombine import recombine as _recombine_impl  # local import to avoid cycles

    return _recombine_impl(md_text=md_text, translated=translated, plan=plan)
