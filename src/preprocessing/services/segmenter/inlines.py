from __future__ import annotations

import re
from typing import Iterator, Optional, Tuple

from .blocks import Block
from .plan import make_id

_re_code_span = re.compile(r"`+")
_re_link_or_img = re.compile(r"(!)?\[")
_re_autolink = re.compile(r"<[^>\n]+>")


def extract_inlines_from_block(
    *,
    md_text: str,
    block: Block,
    kind: str,
    order_start: int,
    translate_labels: bool,
    translate_alts: bool,
    max_chars: Optional[int],
) -> Iterator[tuple[str, tuple[int, int], str, str]]:
    """Extract inline translatable runs from a block.

    Yields tuples of (id, (start, end), text, kind).
    """
    slice_text = md_text[block.start : block.end]
    abs_offset = block.start

    i = 0
    L = len(slice_text)
    inline_index = 0

    while i < L:
        ch = slice_text[i]

        # Autolinks or inline HTML-like <...>
        m_auto = _re_autolink.match(slice_text, i)
        if m_auto:
            i = m_auto.end()
            continue

        # Inline code span: skip entirely
        if ch == "`":
            m_ticks = _re_code_span.match(slice_text, i)
            assert m_ticks is not None
            ticks = m_ticks.group(0)
            i = m_ticks.end()
            close_idx = slice_text.find(ticks, i)
            if close_idx == -1:
                return
            i = close_idx + len(ticks)
            continue

        # Link or image label
        m = (
            _re_link_or_img.match(slice_text, i)
            if (slice_text[i] == "!" or slice_text[i] == "[")
            else None
        )
        if m:
            is_img = bool(m.group(1))
            label_start = m.end()  # after [
            lbl_end = _find_matching_bracket(slice_text, label_start - 1, "[", "]")
            if lbl_end is None:
                i += 1
                continue
            j = lbl_end + 1
            dest_span = _parse_destination_parens(slice_text, j)
            if (is_img and translate_alts) or ((not is_img) and translate_labels):
                abs_start = abs_offset + label_start
                abs_end = abs_offset + lbl_end
                label_text = md_text[abs_start:abs_end]
                for part_idx, (p0, p1) in enumerate(_split_text(label_text, max_chars)):
                    part_abs_start = abs_start + p0
                    part_abs_end = abs_start + p1
                    seg_id = make_id(
                        block, kind, inline_index, part_idx if p1 - p0 != len(label_text) else None
                    )
                    yield seg_id, (part_abs_start, part_abs_end), md_text[
                        part_abs_start:part_abs_end
                    ], kind
                inline_index += 1
            i = dest_span[1] if dest_span is not None else (lbl_end + 1)
            continue

        # Plain text run
        run_start = i
        while i < L:
            ch2 = slice_text[i]
            if ch2 in "`[<" or _re_link_or_img.match(slice_text, i):
                break
            i += 1
        run_end = i
        if run_end > run_start:
            abs_start = abs_offset + run_start
            abs_end = abs_offset + run_end
            run_text = md_text[abs_start:abs_end]
            if run_text.strip() != "":
                spans = _split_text(run_text, max_chars)
                for part_idx, (p0, p1) in enumerate(spans):
                    part_abs_start = abs_start + p0
                    part_abs_end = abs_start + p1
                    seg_id = make_id(
                        block, kind, inline_index, part_idx if len(spans) > 1 else None
                    )
                    yield seg_id, (part_abs_start, part_abs_end), md_text[
                        part_abs_start:part_abs_end
                    ], kind
                inline_index += 1


def _find_matching_bracket(s: str, open_pos: int, open_ch: str, close_ch: str) -> Optional[int]:
    depth = 0
    i = open_pos
    while i < len(s):
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _parse_destination_parens(s: str, pos: int) -> Optional[tuple[int, int]]:
    i = pos
    while i < len(s) and s[i].isspace():
        i += 1
    if i >= len(s) or s[i] != "(":
        return None
    start = i
    depth = 0
    while i < len(s):
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return (start, i + 1)
        i += 1
    return (start, len(s))


def _split_text(text: str, max_chars: Optional[int]) -> list[tuple[int, int]]:
    L = len(text)
    if not max_chars or L <= max_chars:
        return [(0, L)]
    spans: list[tuple[int, int]] = []
    start = 0
    while start < L:
        end = min(start + max_chars, L)
        if end < L:
            m = list(re.finditer(r"[\.!?:]\s", text[start:end]))
            if m:
                end = start + m[-1].end()
            else:
                m2 = list(re.finditer(r"\s+", text[start:end]))
                if m2:
                    end = start + m2[-1].start()
        if end <= start:
            end = min(start + max_chars, L)
        spans.append((start, end))
        start = end
    return spans
