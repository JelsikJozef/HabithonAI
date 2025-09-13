from __future__ import annotations

from typing import List, Sequence, Tuple

from .blocks import Block


def make_id(block: Block, kind: str, inline_index: int, part: int | None) -> str:
    base = f"b/{block.start}:{kind}/i/{inline_index}"
    if part is not None:
        base += f"#p{part}"
    return base


def assert_non_overlapping(spans: List[Tuple[int, int]]) -> None:
    last_end = -1
    for start, end in spans:
        if start < last_end:
            raise ValueError("Overlapping spans in plan")
        last_end = end


def make_plan_components(
    ordered: Sequence[tuple[str, tuple[int, int]]],
) -> tuple[dict[str, tuple[int, int]], tuple[str, ...]]:
    spans = {sid: span for sid, span in ordered}
    ordered_ids = tuple(sid for sid, _ in ordered)
    return spans, ordered_ids
