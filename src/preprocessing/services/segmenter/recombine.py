from __future__ import annotations

import re
from typing import Mapping

from .api import SegmentPlan


def recombine(*, md_text: str, translated: Mapping[str, str], plan: SegmentPlan) -> str:
    """Recompose the Markdown from the original text and per-segment translations.

    This function replaces the planned spans left-to-right using provided
    translations, preserving Markdown structure outside of text spans.
    """
    expected_ids = set(plan.spans.keys())
    provided_ids = set(translated.keys())

    if expected_ids != provided_ids:
        missing = sorted(expected_ids - provided_ids)
        extra = sorted(provided_ids - expected_ids)
        parts = []
        if missing:
            parts.append(f"missing={missing[:3]}{'...' if len(missing)>3 else ''}")
        if extra:
            parts.append(f"extra={extra[:3]}{'...' if len(extra)>3 else ''}")
        raise ValueError("Translated segments do not match extracted set: " + ", ".join(parts))

    out_chunks: list[str] = []
    cursor = 0

    for sid in plan.ordered_ids:
        start, end = plan.spans[sid]
        if cursor > start:
            raise ValueError("Overlapping spans detected during recomposition")
        out_chunks.append(md_text[cursor:start])
        out_chunks.append(translated[sid])
        cursor = end

    out_chunks.append(md_text[cursor:])
    md_text_new = "".join(out_chunks)

    if not _validate_links_intact(md_text, md_text_new):
        raise ValueError("Link destinations changed unexpectedly")
    if not _validate_tables_intact(md_text, md_text_new):
        raise ValueError("Table structure changed unexpectedly")

    return md_text_new


def _validate_links_intact(original_md: str, recombined_md: str) -> bool:
    link_dest_re = re.compile(r"\]\(([^)]+)\)")
    auto_re = re.compile(r"<[^>\n]+>")
    orig = sorted(link_dest_re.findall(original_md) + auto_re.findall(original_md))
    new = sorted(link_dest_re.findall(recombined_md) + auto_re.findall(recombined_md))
    return orig == new


def _validate_tables_intact(original_md: str, recombined_md: str) -> bool:
    def pipes_per_row(s: str) -> list[int]:
        lines = s.splitlines()
        res: list[int] = []
        in_fence = False
        fence_re = re.compile(r"^\s*([`~]{3,})")
        for ln in lines:
            if fence_re.match(ln):
                in_fence = not in_fence
            if in_fence:
                continue
            if "|" in ln:
                res.append(ln.count("|"))
        return res

    return pipes_per_row(original_md) == pipes_per_row(recombined_md)


from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterator, List, Sequence, Tuple

__all__ = ["Block", "BlockScanner"]


@dataclass(frozen=True)
class Block:
    """Represents a block-level Markdown slice.

    Attributes:
        kind: Kind of the block (heading, paragraph, list_item, blockquote, table,
            code_fence, code_indented, html_block, table_cell).
        start: Absolute start index (inclusive) in the original document.
        end: Absolute end index (exclusive) in the original document.
        meta: Additional metadata for the block; e.g., heading level, row/col for cells.
    """

    kind: str
    start: int
    end: int
    meta: dict[str, Any]


class BlockScanner:
    """Deterministic block-level scanner over Markdown text.

    The scanner emits non-overlapping blocks in source order. Use iter_table_cells
    to further split table blocks into per-cell Block instances.
    """

    _re_atx = re.compile(r"^(#{1,6})\s+(.*)$")
    _re_ulist = re.compile(r"^\s*([*+-])\s+(.*)$")
    _re_olist = re.compile(r"^\s*(\d{1,9})[\.)]\s+(.*)$")
    _re_blockquote = re.compile(r"^\s*>\s?(.*)$")
    _re_code_fence = re.compile(r"^\s*([`~]{3,})([^\n]*)$")
    _re_table_delim = re.compile(r"^\s*\|?(\s*:?[-]{3,}:?\s*\|)+\s*:?[-]{3,}:?\s*\|?\s*$")
    _re_html_start = re.compile(r"^\s*<([A-Za-z][A-Za-z0-9-]*)(\s[^>]*)?>\s*$")
    _re_html_end = re.compile(r"^\s*</([A-Za-z][A-Za-z0-9-]*)>\s*$")

    def __init__(self, md_text: str) -> None:
        self._md = md_text
        self._lines = md_text.splitlines(keepends=True)
        self._line_starts = _compute_line_starts(self._lines)

    def scan_blocks(self) -> Iterator[Block]:
        i = 0
        n = len(self._lines)
        md = self._md
        while i < n:
            line = self._lines[i]
            abs_start = self._line_starts[i]

            # Fenced code block
            m_fence = self._re_code_fence.match(line)
            if m_fence:
                fence = m_fence.group(1)
                i_end = i + 1
                while i_end < n:
                    if line_starts_with(self._lines[i_end], fence):
                        i_end += 1
                        break
                    i_end += 1
                abs_end = self._line_starts[i_end] if i_end <= n else len(md)
                yield Block("code_fence", abs_start, abs_end, {"fence": fence})
                i = i_end
                continue

            # Indented code block
            if line.startswith("    ") or line.startswith("\t"):
                i_end2 = i + 1
                while i_end2 < n and (
                    self._lines[i_end2].startswith("    ") or self._lines[i_end2].startswith("\t")
                ):
                    i_end2 += 1
                abs_end2 = self._line_starts[i_end2] if i_end2 <= n else len(md)
                yield Block("code_indented", abs_start, abs_end2, {})
                i = i_end2
                continue

            # HTML block (simple heuristic)
            if self._re_html_start.match(line):
                m = self._re_html_start.match(line)
                assert m is not None
                tag = m.group(1)
                i_end3 = i + 1
                while i_end3 < n:
                    m_end = self._re_html_end.match(self._lines[i_end3])
                    if m_end and m_end.group(1) == tag:
                        i_end3 += 1
                        break
                    i_end3 += 1
                abs_end3 = self._line_starts[i_end3] if i_end3 <= n else len(md)
                yield Block("html_block", abs_start, abs_end3, {"tag": tag})
                i = i_end3
                continue

            # Heading
            m_h = self._re_atx.match(line)
            if m_h:
                level = len(m_h.group(1))
                yield Block(
                    "heading",
                    abs_start,
                    self._line_starts[i + 1] if i + 1 <= n else len(md),
                    {"level": level},
                )
                i += 1
                continue

            # Table: lookahead for delimiter row
            if "|" in line and i + 1 < n and self._re_table_delim.match(self._lines[i + 1]):
                i_end4 = i + 2
                while (
                    i_end4 < n
                    and "|" in self._lines[i_end4]
                    and not self._lines[i_end4].strip().startswith("#")
                    and not self._re_code_fence.match(self._lines[i_end4])
                ):
                    if self._lines[i_end4].strip() == "":
                        break
                    i_end4 += 1
                abs_end4 = self._line_starts[i_end4] if i_end4 <= n else len(md)
                yield Block("table", abs_start, abs_end4, {})
                i = i_end4
                continue

            # Blockquote
            m_bq = self._re_blockquote.match(line)
            if m_bq:
                i_end5 = i + 1
                while i_end5 < n and self._re_blockquote.match(self._lines[i_end5]):
                    i_end5 += 1
                abs_end5 = self._line_starts[i_end5] if i_end5 <= n else len(md)
                yield Block("blockquote", abs_start, abs_end5, {})
                i = i_end5
                continue

            # List item
            m_ul = self._re_ulist.match(line)
            m_ol = self._re_olist.match(line) if not m_ul else None
            if m_ul or m_ol:
                marker = m_ul.group(1) if m_ul else m_ol.group(1)
                yield Block(
                    "list_item",
                    abs_start,
                    self._line_starts[i + 1] if i + 1 <= n else len(md),
                    {"marker": marker},
                )
                i += 1
                continue

            # Blank line
            if line.strip() == "":
                i += 1
                continue

            # Paragraph
            i_end6 = i + 1
            while i_end6 < n:
                nxt = self._lines[i_end6]
                if nxt.strip() == "":
                    break
                if (
                    self._re_atx.match(nxt)
                    or self._re_code_fence.match(nxt)
                    or self._re_blockquote.match(nxt)
                    or self._re_ulist.match(nxt)
                    or self._re_olist.match(nxt)
                ):
                    break
                if (
                    "|" in nxt
                    and i_end6 + 1 < n
                    and self._re_table_delim.match(self._lines[i_end6 + 1])
                ):
                    break
                i_end6 += 1
            abs_end6 = self._line_starts[i_end6] if i_end6 <= n else len(md)
            yield Block("paragraph", abs_start, abs_end6, {})
            i = i_end6

    def iter_table_cells(self, block: Block) -> Iterator[Block]:
        """Yield table cell Blocks within a table block."""
        if block.kind != "table":
            return
        text = self._md[block.start : block.end]
        lines = text.splitlines(keepends=True)
        if len(lines) < 2:
            return
        abs_line_start = block.start
        for idx, line in enumerate(lines):
            is_delim = self._re_table_delim.match(line) is not None
            if is_delim or line.strip() == "":
                abs_line_start += len(line)
                continue
            cell_spans = _split_table_row_cells(line)
            for col, (c_start, c_end) in enumerate(cell_spans):
                cell_text = line[c_start:c_end]
                left_trim = len(cell_text) - len(cell_text.lstrip(" "))
                right_trim = len(cell_text) - len(cell_text.rstrip(" "))
                c_abs_start = abs_line_start + c_start + left_trim
                c_abs_end = abs_line_start + c_end - right_trim
                if c_abs_end <= c_abs_start:
                    continue
                yield Block("table_cell", c_abs_start, c_abs_end, {"row": idx, "col": col})
            abs_line_start += len(line)


def _compute_line_starts(lines: Sequence[str]) -> List[int]:
    starts: List[int] = [0]
    total = 0
    for ln in lines:
        total += len(ln)
        starts.append(total)
    return starts


def line_starts_with(line: str, fence: str) -> bool:
    stripped = line.lstrip()
    if not stripped:
        return False
    if stripped[0] not in "`~":
        return False
    ch = stripped[0]
    i = 0
    while i < len(stripped) and stripped[i] == ch:
        i += 1
    return ch == fence[0] and i >= len(fence)


def _split_table_row_cells(line: str) -> List[Tuple[int, int]]:
    positions = [i for i, ch in enumerate(line) if ch == "|"]
    spans: List[Tuple[int, int]] = []
    last = 0
    if not positions:
        return []
    for pos in positions:
        spans.append((last, pos))
        last = pos + 1
    spans.append((last, len(line.rstrip("\n"))))
    if line.startswith("|") and spans:
        spans = spans[1:]
    stripped = line.rstrip("\n")
    if stripped.endswith("|") and spans:
        spans = spans[:-1]
    return spans
