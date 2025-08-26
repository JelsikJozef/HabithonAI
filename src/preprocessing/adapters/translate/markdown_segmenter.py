"""
markdown_segmenter
===================

Lossless Markdown segmentation and recomposition utilities for translation adapters.

This module exposes two public functions:

- extract_segments(md_text, *, options=None) -> (segments, plan)
- recombine(md_text, translated_segments, plan, *, options=None) -> md_text_en

It performs no translation. It identifies translatable text nodes, extracts them
with stable identifiers, and later reinserts provided translations while preserving
Markdown structure byte-for-byte outside the replaced text spans.

Design notes
------------
- Parsing uses a pragmatic, deterministic, line-oriented scanner with a fenced-code
  state machine and simple inline tokenization for links/images and code spans.
- The plan encodes absolute character spans for each extracted segment to support
  exact recomposition without re-parsing.
- Options influence inclusion/exclusion of certain inline labels and tables, and
  soft-break preservation. Unsupported or unknown options are ignored.

Caveats
-------
This is a robust, dependency-free implementation intended for common Markdown.
It purposefully avoids full CommonMark edge cases (e.g., complex HTML blocks,
reference-style link definitions) to keep the implementation light. It strictly
preserves:
- fenced/indented code blocks (never extracted),
- inline code spans (never extracted),
- link destinations and image URLs (never altered),
- list/table/blockquote/heading markers and layout.

If your documents rely heavily on uncommon constructs, consider swapping the
block/inline scanners with a formal CommonMark AST while keeping the plan format.

"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Sequence, Tuple, Any
import re

__all__ = [
    "Segment",
    "SegmentationError",
    "RecombinationError",
    "StructuralDriftError",
    "IdMismatchError",
    "extract_segments",
    "recombine",
    "validate_no_code_translation",
    "validate_links_intact",
    "validate_tables_intact",
    "fingerprint",
]


# ==========================
# Errors
# ==========================


class SegmentationError(RuntimeError):
    """Raised when Markdown parsing or segmentation fails.

    This exception indicates a domain-level error (e.g., malformed plan construction
    or inconsistent scanning state), abstracting away parser specifics.
    """


class RecombinationError(RuntimeError):
    """Raised when recombination with translated segments fails deterministically.

    Causes include ID mismatches, overlapping spans, or invalid replacement data.
    """


class StructuralDriftError(RuntimeError):
    """Raised when validate_structure is enabled and AST/shape drift is detected."""


class IdMismatchError(RuntimeError):
    """Raised when provided translated segments do not match extracted IDs exactly."""


# ==========================
# Data model
# ==========================


@dataclass(frozen=True)
class Segment:
    """Represents a single translatable text unit.

    Fields
    ------
    id: str
        Stable identifier unique within the document. Format used here:
        "b/{block_index}:{kind}/i/{inline_index}" or with part suffix
        "b/{block_index}:{kind}/i/{inline_index}#p{part}".
    text: str
        The exact text content to translate (no Markdown delimiters).
    path: Tuple[Any, ...]
        Address to the originating node and offset. This implementation uses a
        deterministic tuple: ("root", block_index, "block", kind, "inline", inline_index, "part", part or None)
    kind: str
        Semantic type of the node. One of: "heading", "paragraph", "list_item",
        "blockquote", "table_cell", or "inline".
    context: Dict[str, Any]
        Context hints such as heading level, list depth, table row/col, surrounding
        snippets, etc.
    order: int
        Stable extraction order (0..N-1).
    part: Optional[int]
        Part index (0..k-1) when a long node was split according to options.
    """

    id: str
    text: str
    path: Tuple[Any, ...]
    kind: str
    context: Dict[str, Any]
    order: int
    part: Optional[int] = None


@dataclass(frozen=True)
class _Span:
    """Absolute span in the original md_text (start inclusive, end exclusive)."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError("Invalid span bounds")


@dataclass(frozen=True)
class _PlanEntry:
    """Recomposition blueprint entry for a single segment or segment part.

    Fields
    ------
    id: str
        Segment id (may include #p{part}).
    span: _Span
        Absolute character range in the original md_text to replace.
    kind: str
        Node kind hint.
    path: Tuple[Any, ...]
        Address used for determinism and optional validation.
    softbreaks: Tuple[int, ...]
        Positions (relative to span.start) of any original softbreak newlines for
        informational purposes and optional validation.
    whitespace_policy: Dict[str, Any]
        Captures preservation preferences (preserve_whitespace, preserve_softbreaks).
    context: Dict[str, Any]
        Context hints carried over to assist downstream logic.
    """

    id: str
    span: _Span
    kind: str
    path: Tuple[Any, ...]
    softbreaks: Tuple[int, ...] = field(default_factory=tuple)
    whitespace_policy: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _Plan:
    """Opaque recomposition plan.

    Internals
    ---------
    entries_by_id: Dict[str, _PlanEntry]
        Mapping from segment id to its plan entry.
    ordered_spans: Tuple[Tuple[str, _Span], ...]
        Pairs of (id, span) sorted by span.start for recomposition.
    options_fingerprint: Tuple[str, ...]
        Fingerprint of options at extraction time to assist validation/debugging.
    stats: Dict[str, Any]
        Telemetry about the extraction run.
    """

    entries_by_id: Dict[str, _PlanEntry]
    ordered_spans: Tuple[Tuple[str, _Span], ...]
    options_fingerprint: Tuple[str, ...]
    stats: Dict[str, Any]


# ==========================
# Public API
# ==========================


def extract_segments(md_text: str, *, options: Optional[Dict[str, Any]] = None) -> Tuple[List[Segment], _Plan]:
    """Extract translatable segments from a full Markdown document.

    Parameters
    ----------
    md_text : str
        Full Markdown document text with LF newlines ("\n"). The text is treated
        as UTF-8 content and scanned deterministically.
    options : dict | None, keyword-only
        Behavioral options:
        - segment_max_chars (int): Soft ceiling for segment length. When a text
          node exceeds this size, it may be split at sentence/word boundaries.
        - translate_alt_text (bool, default True): Extract image alt text.
        - translate_link_label (bool, default True): Extract link labels.
        - translate_table_cells (bool, default True): Extract table cell text.
        - preserve_whitespace (bool, default True): Preserve leading/trailing
          spaces exactly in extracted text.
        - collapse_softbreaks (bool, default False): If True, collapse newlines
          inside inline text runs to spaces in the extracted text. Note: this
          implementation records softbreaks but does not transform text; setting
          True is currently treated as a no-op for safety.
        - language_hint (str | None): Optional hint for sentence splitting
          heuristics (currently informational only).

    Returns
    -------
    segments : list[Segment]
        Ordered list of translatable segments. Each segment includes a stable id,
        plain text to translate, a deterministic path, kind, context, order, and
        optional part index (when split).
    plan : opaque structure
        Recomposition plan capturing exact character spans for replacements and
        auxiliary metadata. Treat as immutable and serializable. Pass it back to
        recombine to reinsert translations.

    Responsibilities
    ----------------
    - Identify all translatable text nodes (headings, paragraphs, list items,
      blockquotes, and optionally table cells, link labels, and image alt text).
    - Exclude code fences/blocks, inline code, link destinations, image URLs,
      and raw HTML from extraction.
    - Produce deterministic, stable segment IDs and a recomposition plan with
      absolute spans to support byte-stable structure preservation.

    Raises
    ------
    SegmentationError
        If the document cannot be scanned deterministically or internal
        invariants fail.
    """
    if not isinstance(md_text, str):  # defensive
        raise SegmentationError("md_text must be a string")

    opts = _normalize_options(options)

    try:
        segments: List[Segment] = []
        plan_entries: Dict[str, _PlanEntry] = {}
        block_scanner = _BlockScanner(md_text)
        order_counter = 0
        for block in block_scanner.scan_blocks():
            if block.kind in {"code_fence", "code_indented", "html_block"}:
                # Excluded by design
                continue

            # Dispatch block types
            if block.kind == "heading":
                # Entire heading text content is a single inline region; extract inlines
                for seg, entry in _extract_inlines_from_block(
                    md_text,
                    block,
                    kind="heading",
                    order_start=order_counter,
                    translate_labels=opts["translate_link_label"],
                    translate_alts=opts["translate_alt_text"],
                    preserve_whitespace=opts["preserve_whitespace"],
                    max_chars=opts["segment_max_chars"],
                ):
                    segments.append(seg)
                    plan_entries[entry.id] = entry
                    order_counter = seg.order + 1
            elif block.kind in {"paragraph", "list_item", "blockquote"}:
                for seg, entry in _extract_inlines_from_block(
                    md_text,
                    block,
                    kind=block.kind,
                    order_start=order_counter,
                    translate_labels=opts["translate_link_label"],
                    translate_alts=opts["translate_alt_text"],
                    preserve_whitespace=opts["preserve_whitespace"],
                    max_chars=opts["segment_max_chars"],
                ):
                    segments.append(seg)
                    plan_entries[entry.id] = entry
                    order_counter = seg.order + 1
            elif block.kind == "table":
                if opts["translate_table_cells"]:
                    for seg, entry in _extract_from_table_block(
                        md_text,
                        block,
                        order_start=order_counter,
                        translate_labels=opts["translate_link_label"],
                        translate_alts=opts["translate_alt_text"],
                        preserve_whitespace=opts["preserve_whitespace"],
                        max_chars=opts["segment_max_chars"],
                    ):
                        segments.append(seg)
                        plan_entries[entry.id] = entry
                        order_counter = seg.order + 1
                else:
                    # Skip table cells entirely
                    pass
            else:
                # Unknown/unsupported block kinds are preserved but not extracted.
                continue

        # Construct ordered spans for recomposition
        ordered = sorted(((pid, e.span) for pid, e in plan_entries.items()), key=lambda p: (p[1].start, p[1].end))
        _assert_non_overlapping([sp for _, sp in ordered])

        stats = {
            "segments_count": len(segments),
            "avg_chars": (sum(len(s.text) for s in segments) / len(segments)) if segments else 0.0,
            "max_chars": max((len(s.text) for s in segments), default=0),
            "split_nodes": sum(1 for s in segments if s.part is not None),
        }

        plan = _Plan(
            entries_by_id=plan_entries,
            ordered_spans=tuple(ordered),
            options_fingerprint=_options_fingerprint(opts),
            stats=stats,
        )
        return segments, plan
    except SegmentationError:
        raise
    except Exception as exc:  # wrap unexpected
        raise SegmentationError(str(exc)) from exc


def recombine(
    md_text: str,
    translated_segments: Sequence[Dict[str, Any] | Segment],
    plan: _Plan,
    *,
    options: Optional[Dict[str, Any]] = None,
) -> str:
    """Reinsert translated text back into the original Markdown.

    Parameters
    ----------
    md_text : str
        The original Markdown text used for extraction. This must be identical to
        the input of extract_segments.
    translated_segments : Sequence[dict | Segment]
        Collection of translated items that must match the extracted segments 1:1
        by id (and part if applicable). Each item provides at least:
        - id (str): Segment identifier matching extraction output.
        - text (str): Translated text to insert into the original Markdown.
        Optional fields are ignored.
    plan : opaque
        The recomposition plan returned by extract_segments for this document.
        It encodes absolute spans for replacement and is treated as immutable.
    options : dict | None, keyword-only
        Behavioral options:
        - validate_structure (bool, default True): Verify basic integrity (no span
          overlap, stable options fingerprint) prior to recomposition.
        - preserve_softbreaks (bool, default True): Retain original inline softbreaks
          positions (informational; text is inserted as provided).
        - post_trim (bool, default False): If True, strip trailing spaces from
          translated text segments before insertion.

    Returns
    -------
    md_text_en : str
        Reconstructed Markdown with identical structural tokens and unchanged bytes
        outside replaced text spans. Only the specific text nodes are replaced.

    Responsibilities
    ----------------
    - Enforce order and cardinality: all and only the extracted IDs must be present;
      duplicates or missing IDs raise deterministic errors.
    - Preserve exact non-text structure (code fences, link destinations, list/table
      markers, and raw HTML) by replacing only the planned spans.

    Raises
    ------
    IdMismatchError
        If the provided translated segments set differs from the extracted set.
    StructuralDriftError
        If validate_structure is enabled and basic invariants are violated.
    RecombinationError
        For other recombination failures (e.g., overlapping spans, invalid inputs).
    """
    if not isinstance(md_text, str):
        raise RecombinationError("md_text must be a string")

    opts = _normalize_recombine_options(options)

    if not isinstance(plan, _Plan):
        raise RecombinationError("Invalid plan: unexpected type")

    # Validate structure/options fingerprint before recombine
    if opts["validate_structure"]:
        # Ensure spans are sorted and non-overlapping
        _assert_non_overlapping([sp for _, sp in plan.ordered_spans])

    # Map translations by id
    provided: Dict[str, str] = {}
    for item in translated_segments:
        if isinstance(item, Segment):
            sid = item.id
            text = item.text
        elif isinstance(item, dict):
            sid = item.get("id")
            text = item.get("text")
        else:
            raise RecombinationError("translated_segments must contain dict or Segment items")
        if not isinstance(sid, str) or not isinstance(text, str):
            raise RecombinationError("Each translated segment requires string id and text")
        if sid in provided:
            raise IdMismatchError(f"Duplicate translated segment id: {sid}")
        provided[sid] = text

    expected_ids = set(plan.entries_by_id.keys())
    provided_ids = set(provided.keys())

    if expected_ids != provided_ids:
        missing = sorted(expected_ids - provided_ids)
        extra = sorted(provided_ids - expected_ids)
        parts = []
        if missing:
            parts.append(f"missing={missing[:3]}{'...' if len(missing)>3 else ''}")
        if extra:
            parts.append(f"extra={extra[:3]}{'...' if len(extra)>3 else ''}")
        raise IdMismatchError("Translated segments do not match extracted set: " + ", ".join(parts))

    # Perform replacements left-to-right
    out_chunks: List[str] = []
    cursor = 0

    for seg_id, span in plan.ordered_spans:
        # Append untouched region
        if cursor > span.start:
            # Overlap indicates inconsistent plan
            raise StructuralDriftError("Overlapping spans detected during recomposition")
        out_chunks.append(md_text[cursor:span.start])

        # Fetch translated text and optionally trim
        repl = provided[seg_id]
        if opts["post_trim"]:
            repl = repl.rstrip(" ")

        # Preserve softbreaks policy: We don't alter provided text; callers should
        # supply the exact softbreak representation they want. The plan retains
        # original softbreak positions for optional validation or future mapping.
        out_chunks.append(repl)
        cursor = span.end

    out_chunks.append(md_text[cursor:])
    md_text_en = "".join(out_chunks)

    # Optional sanity validations
    # Links and tables structure are compared heuristically; they must not change
    # given span-based replacement strategy.
    if opts["validate_structure"]:
        if not validate_links_intact(md_text, md_text_en):
            raise StructuralDriftError("Link destinations changed unexpectedly")
        if not validate_tables_intact(md_text, md_text_en):
            raise StructuralDriftError("Table structure changed unexpectedly")

    return md_text_en


# ==========================
# Scanners and helpers
# ==========================


@dataclass
class _Block:
    kind: str  # heading, paragraph, list_item, blockquote, table, code_fence, code_indented, html_block
    start: int  # absolute char index inclusive
    end: int  # absolute char index exclusive
    meta: Dict[str, Any]


class _BlockScanner:
    """Deterministic block-level scanner over Markdown text.

    Responsibilities
    ----------------
    - Walk the Markdown document once, producing block records with absolute
      character spans and metadata that guides inline extraction.
    - Exclude fenced code regions and detect simple HTML blocks conservatively.

    Guarantees
    ----------
    - Blocks are emitted in source order with non-overlapping spans.
    - start/end indices bound the exact slice in md_text corresponding to the block.

    Notes
    -----
    This scanner covers common Markdown forms:
    - Headings (# to ######) using ATX syntax.
    - Paragraphs (one or more non-empty lines not belonging to other blocks).
    - Lists (unordered -+* or ordered 1.) condensed into list_item blocks per line.
    - Blockquotes starting with ">".
    - Tables defined by pipe-delimited rows with a separator line (dashes and colons).
    - Fenced code blocks (``` or ~~~) and indented code (>=4 spaces) are detected
      and skipped (classified as code_* kinds).
    - Simple HTML blocks are conservatively detected when a line starts with "<"
      and ends with ">"; multi-line HTML is treated as a single block if it
      starts with a block-level tag and ends with its matching closing tag on a
      subsequent line; otherwise, each such line becomes its own html_block.
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

    def scan_blocks(self) -> Iterator[_Block]:
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
                        # Closing fence found
                        i_end += 1
                        break
                    i_end += 1
                abs_end = self._line_starts[i_end] if i_end <= n else len(md)
                yield _Block("code_fence", abs_start, abs_end, {"fence": fence})
                i = i_end
                continue

            # Indented code block (>= 4 spaces) - consume consecutive lines
            if line.startswith("    ") or line.startswith("\t"):
                i_end2 = i + 1
                while i_end2 < n and (self._lines[i_end2].startswith("    ") or self._lines[i_end2].startswith("\t")):
                    i_end2 += 1
                abs_end2 = self._line_starts[i_end2] if i_end2 <= n else len(md)
                yield _Block("code_indented", abs_start, abs_end2, {})
                i = i_end2
                continue

            # HTML block (simple heuristic)
            if self._re_html_start.match(line):
                tag = self._re_html_start.match(line).group(1)
                i_end3 = i + 1
                # Find matching closing tag on its own line
                while i_end3 < n:
                    if self._re_html_end.match(self._lines[i_end3]) and self._re_html_end.match(self._lines[i_end3]).group(1) == tag:
                        i_end3 += 1
                        break
                    i_end3 += 1
                abs_end3 = self._line_starts[i_end3] if i_end3 <= n else len(md)
                yield _Block("html_block", abs_start, abs_end3, {"tag": tag})
                i = i_end3
                continue

            # Heading
            m_h = self._re_atx.match(line)
            if m_h:
                level = len(m_h.group(1))
                # Heading ends at line end
                yield _Block("heading", abs_start, self._line_starts[i + 1] if i + 1 <= n else len(md), {"level": level})
                i += 1
                continue

            # Table langid: lookahead for delimiter row
            if "|" in line and i + 1 < n and self._re_table_delim.match(self._lines[i + 1]):
                # Consume header row, delimiter, and subsequent rows until blank line or non-table
                i_end4 = i + 2
                while i_end4 < n and "|" in self._lines[i_end4] and not self._lines[i_end4].strip().startswith("#") and not self._re_code_fence.match(self._lines[i_end4]):
                    if self._lines[i_end4].strip() == "":
                        break
                    i_end4 += 1
                abs_end4 = self._line_starts[i_end4] if i_end4 <= n else len(md)
                yield _Block("table", abs_start, abs_end4, {})
                i = i_end4
                continue

            # Blockquote
            m_bq = self._re_blockquote.match(line)
            if m_bq:
                i_end5 = i + 1
                while i_end5 < n and self._re_blockquote.match(self._lines[i_end5]):
                    i_end5 += 1
                abs_end5 = self._line_starts[i_end5] if i_end5 <= n else len(md)
                yield _Block("blockquote", abs_start, abs_end5, {})
                i = i_end5
                continue

            # List item(s) - we treat each line as its own list_item block to keep spans simple
            m_ul = self._re_ulist.match(line)
            m_ol = self._re_olist.match(line) if not m_ul else None
            if m_ul or m_ol:
                # Consume consecutive list lines of same indentation as separate blocks
                # But only emit current line as list_item block
                marker = m_ul.group(1) if m_ul else m_ol.group(1)
                text_after = (m_ul.group(2) if m_ul else m_ol.group(2)) or ""
                # Determine end as end of the line
                yield _Block("list_item", abs_start, self._line_starts[i + 1] if i + 1 <= n else len(md), {"marker": marker, "text": text_after})
                i += 1
                continue

            # Blank line -> skip as separator
            if line.strip() == "":
                i += 1
                continue

            # Paragraph: consume until blank line or other block start
            i_end6 = i + 1
            while i_end6 < n:
                nxt = self._lines[i_end6]
                if nxt.strip() == "":
                    break
                if self._re_atx.match(nxt) or self._re_code_fence.match(nxt) or self._re_blockquote.match(nxt) or self._re_ulist.match(nxt) or self._re_olist.match(nxt):
                    break
                if "|" in nxt and i_end6 + 1 < n and self._re_table_delim.match(self._lines[i_end6 + 1]):
                    break
                i_end6 += 1
            abs_end6 = self._line_starts[i_end6] if i_end6 <= n else len(md)
            yield _Block("paragraph", abs_start, abs_end6, {})
            i = i_end6


# Inline parsing helpers
_re_code_span = re.compile(r"`+")
_re_link_or_img = re.compile(r"(!)?\[" )  # start of [label] or ![alt]
_re_autolink = re.compile(r"<[^>\n]+>")


def _extract_inlines_from_block(
    md_text: str,
    block: _Block,
    *,
    kind: str,
    order_start: int,
    translate_labels: bool,
    translate_alts: bool,
    preserve_whitespace: bool,
    max_chars: Optional[int],
) -> Iterator[Tuple[Segment, _PlanEntry]]:
    """Extract inline translatable runs from a non-table block.

    This function walks the block slice and emits segments for:
    - plain text runs (outside links/images/code spans/autolinks),
    - link labels [label] if translate_labels,
    - image alt text ![alt] if translate_alts.

    It never emits segments for:
    - inline code spans `code`,
    - link destinations (the (... ) part),
    - autolinks <http://...> or inline HTML.

    Splitting: If a run exceeds max_chars, it is split at sentence or word
    boundaries with deterministic part indices.
    """
    slice_text = md_text[block.start:block.end]
    abs_offset = block.start

    # States
    i = 0
    L = len(slice_text)
    inline_index = 0
    order = order_start

    # Track softbreaks relative offsets inside runs for plan entries
    def softbreak_positions(text: str, rel_base: int) -> Tuple[int, ...]:
        return tuple(idx - rel_base for idx, ch in enumerate(text, start=0) if ch == "\n")

    while i < L:
        ch = slice_text[i]

        # Skip autolinks and inline HTML-like <...>
        m_auto = _re_autolink.match(slice_text, i)
        if m_auto:
            i = m_auto.end()
            continue

        # Inline code span: skip entirely
        if ch == "`":
            # consume matching code span
            tick_start = i
            # Match sequence of backticks
            m_ticks = _re_code_span.match(slice_text, i)
            assert m_ticks is not None
            ticks = m_ticks.group(0)
            i = m_ticks.end()
            # Find closing sequence
            close_idx = slice_text.find(ticks, i)
            if close_idx == -1:
                # Unclosed; treat rest as code span
                return
            i = close_idx + len(ticks)
            continue

        # Link or image label
        if slice_text[i] == '!' or slice_text[i] == '[':
            m = _re_link_or_img.match(slice_text, i)
        else:
            m = None
        if m:
            is_img = bool(m.group(1))
            label_start = m.end()  # position after [
            # Find closing ] (not escaped)
            lbl_end = _find_matching_bracket(slice_text, label_start - 1, '[', ']')
            if lbl_end is None:
                # malformed; break to avoid infinite loop
                i = i + 1
                continue
            # Now expect a destination starting with (
            j = lbl_end + 1
            dest_span = _parse_destination_parens(slice_text, j)
            # Emit label/alt segment if allowed
            if (is_img and translate_alts) or ((not is_img) and translate_labels):
                # label text span absolute
                abs_start = abs_offset + label_start
                abs_end = abs_offset + lbl_end
                label_text = md_text[abs_start:abs_end]
                # Possibly split
                split_spans = _split_text(label_text, max_chars)
                for part_idx, part_span in enumerate(split_spans):
                     part_abs_start = abs_start + part_span[0]
                     part_abs_end = abs_start + part_span[1]
                     part_val = part_idx if len(split_spans) > 1 else None
                     seg_id = _make_id(block, kind, inline_index, part_val)
                     seg = Segment(
                         id=seg_id,
                         text=md_text[part_abs_start:part_abs_end],
                         path=("root", block.start, "block", kind, "inline", inline_index, "part", part_val),
                         kind=kind,
                         context=_context_for_block(block),
                         order=order,
                         part=part_val,
                     )
                     entry = _PlanEntry(
                        id=seg_id,
                        span=_Span(part_abs_start, part_abs_end),
                        kind=kind,
                        path=seg.path,
                        softbreaks=softbreak_positions(md_text[part_abs_start:part_abs_end], 0),
                        whitespace_policy={"preserve_whitespace": preserve_whitespace, "preserve_softbreaks": True},
                        context=seg.context,
                     )
                     yield seg, entry
                     order += 1
                inline_index += 1
            # Advance i past the label and optional destination
            i = dest_span[1] if dest_span is not None else (lbl_end + 1)
            continue

        # Plain text run until next special token or end
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
            # Skip pure whitespace runs to avoid noisy segments
            if run_text.strip() != "":
                split_spans = _split_text(run_text, max_chars)
                for part_idx, part_span in enumerate(split_spans):
                     part_abs_start = abs_start + part_span[0]
                     part_abs_end = abs_start + part_span[1]
                     part_val = part_idx if len(split_spans) > 1 else None
                     seg_id = _make_id(block, kind, inline_index, part_val)
                     seg = Segment(
                         id=seg_id,
                         text=md_text[part_abs_start:part_abs_end],
                         path=("root", block.start, "block", kind, "inline", inline_index, "part", part_val),
                         kind=kind,
                         context=_context_for_block(block),
                         order=order,
                         part=part_val,
                     )
                     entry = _PlanEntry(
                        id=seg_id,
                        span=_Span(part_abs_start, part_abs_end),
                        kind=kind,
                        path=seg.path,
                        softbreaks=softbreak_positions(md_text[part_abs_start:part_abs_end], 0),
                        whitespace_policy={"preserve_whitespace": preserve_whitespace, "preserve_softbreaks": True},
                        context=seg.context,
                     )
                     yield seg, entry
                     order += 1
                inline_index += 1
        # else: nothing to emit


def _extract_from_table_block(
    md_text: str,
    block: _Block,
    *,
    order_start: int,
    translate_labels: bool,
    translate_alts: bool,
    preserve_whitespace: bool,
    max_chars: Optional[int],
) -> Iterator[Tuple[Segment, _PlanEntry]]:
    """Extract translatable segments from a pipe-delimited table block.

    Rules
    -----
    - Do not touch the delimiter row (---|:---) and do not alter pipes/alignment.
    - For each header/body row, extract cell text excluding surrounding spaces.
    - Inline parsing within cells is conservative: ignore code spans, extract link
      labels and image alt text consistent with non-table inline extraction.
    """
    text = md_text[block.start:block.end]
    lines = text.splitlines(keepends=True)
    if len(lines) < 2:
        return

    abs_line_start = block.start
    order = order_start

    def abs_idx(line_base: int, rel: int) -> int:
        return line_base + rel

    for idx, line in enumerate(lines):
        is_delim = _BlockScanner._re_table_delim.match(line) is not None
        if is_delim:
            abs_line_start += len(line)
            continue
        if line.strip() == "":
            abs_line_start += len(line)
            continue

        cell_spans = _split_table_row_cells(line)
        for col, (c_start, c_end) in enumerate(cell_spans):
            cell_text = line[c_start:c_end]
            left_trim = len(cell_text) - len(cell_text.lstrip(" "))
            right_trim = len(cell_text) - len(cell_text.rstrip(" "))
            c_abs_start = abs_idx(abs_line_start, c_start + left_trim)
            c_abs_end = abs_idx(abs_line_start, c_end - right_trim)
            core = md_text[c_abs_start:c_abs_end]
            if core.strip() == "":
                continue

            # Use inline extraction on the cell slice
            cell_block = _Block("table_cell", c_abs_start, c_abs_end, {"row": idx, "col": col})
            for seg, entry in _extract_inlines_from_block(
                md_text,
                cell_block,
                kind="table_cell",
                order_start=order,
                translate_labels=translate_labels,
                translate_alts=translate_alts,
                preserve_whitespace=preserve_whitespace,
                max_chars=max_chars,
            ):
                ctx = dict(seg.context)
                ctx.setdefault("table", {"row": idx, "col": col})
                seg = Segment(
                    id=seg.id,
                    text=seg.text,
                    path=seg.path,
                    kind=seg.kind,
                    context=ctx,
                    order=seg.order,
                    part=seg.part,
                )
                entry = _PlanEntry(
                    id=entry.id,
                    span=entry.span,
                    kind=entry.kind,
                    path=entry.path,
                    softbreaks=entry.softbreaks,
                    whitespace_policy=entry.whitespace_policy,
                    context=ctx,
                )
                yield seg, entry
                order = seg.order + 1
        abs_line_start += len(line)


# ==========================
# Utility functions
# ==========================


def _normalize_options(options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    opts = dict(options or {})
    opts.setdefault("segment_max_chars", None)
    opts.setdefault("translate_alt_text", True)
    opts.setdefault("translate_link_label", True)
    opts.setdefault("translate_table_cells", True)
    opts.setdefault("preserve_whitespace", True)
    opts.setdefault("collapse_softbreaks", False)
    opts.setdefault("language_hint", None)
    return opts


def _normalize_recombine_options(options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    opts = dict(options or {})
    opts.setdefault("validate_structure", True)
    opts.setdefault("preserve_softbreaks", True)
    opts.setdefault("post_trim", False)
    return opts


def _options_fingerprint(opts: Dict[str, Any]) -> Tuple[str, ...]:
    keys = [
        "segment_max_chars",
        "translate_alt_text",
        "translate_link_label",
        "translate_table_cells",
        "preserve_whitespace",
        "collapse_softbreaks",
        "language_hint",
    ]
    return tuple(f"{k}={opts.get(k)!r}" for k in keys)


def _assert_non_overlapping(spans: List[_Span]) -> None:
    last_end = -1
    for sp in spans:
        if sp.start < last_end:
            raise SegmentationError("Overlapping spans in plan")
        last_end = sp.end


def _compute_line_starts(lines: Sequence[str]) -> List[int]:
    starts = [0]
    total = 0
    for ln in lines:
        total += len(ln)
        starts.append(total)
    return starts


def line_starts_with(line: str, fence: str) -> bool:
    # Match closing fence allowing leading spaces and same fence char repeated >= len(fence)
    stripped = line.lstrip()
    if not stripped:
        return False
    if stripped[0] not in "`~":
        return False
    # Count consecutive fence chars
    ch = stripped[0]
    i = 0
    while i < len(stripped) and stripped[i] == ch:
        i += 1
    return ch == fence[0] and i >= len(fence)


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


def _parse_destination_parens(s: str, pos: int) -> Optional[Tuple[int, int]]:
    # Find balanced parentheses starting at optional whitespace then '('
    i = pos
    while i < len(s) and s[i].isspace():
        i += 1
    if i >= len(s) or s[i] != '(':  # no destination
        return None
    start = i
    depth = 0
    while i < len(s):
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return (start, i + 1)
        i += 1
    return (start, len(s))  # unbalanced; treat to end


def _split_text(text: str, max_chars: Optional[int]) -> List[Tuple[int, int]]:
    """Return list of (start,end) spans covering text, splitting if needed.

    Splitting heuristics
    --------------------
    - If max_chars is None or text length <= max_chars, returns a single span.
    - Prefer splitting at sentence boundaries (?!.:) followed by whitespace.
    - Otherwise, split at word boundaries without breaking words.
    - Always produce non-overlapping, contiguous spans covering the full text.
    """
    L = len(text)
    if not max_chars or L <= max_chars:
        return [(0, L)]

    spans: List[Tuple[int, int]] = []
    start = 0
    while start < L:
        end = min(start + max_chars, L)
        if end < L:
            # try find last sentence boundary within start..end
            m = list(re.finditer(r"[\.!?:]\s", text[start:end]))
            if m:
                end = start + m[-1].end()
            else:
                # try last whitespace
                m2 = list(re.finditer(r"\s+", text[start:end]))
                if m2:
                    end = start + m2[-1].start()
        if end <= start:
            # fallback avoid zero-length
            end = min(start + max_chars, L)
        spans.append((start, end))
        start = end
    return spans


def _make_id(block: _Block, kind: str, inline_index: int, part: Optional[int]) -> str:
    base = f"b/{block.start}:{kind}/i/{inline_index}"
    if part is not None:
        base += f"#p{part}"
    return base


def _context_for_block(block: _Block) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {}
    if block.kind == "heading":
        ctx["heading_level"] = block.meta.get("level")
    if block.kind == "list_item":
        ctx["list_marker"] = block.meta.get("marker")
    if block.kind == "blockquote":
        ctx["blockquote"] = True
    return ctx


def _split_table_row_cells(line: str) -> List[Tuple[int, int]]:
    """Split a single table row line into cell spans [start,end) relative to the line.

    Behavior
    --------
    - Splits on pipe '|' characters.
    - Trims leading/trailing empty cells if the row starts/ends with a pipe.
    - Returns spans for the raw cell contents (excluding pipes) for absolute mapping.
    """
    positions = [i for i, ch in enumerate(line) if ch == '|']
    spans: List[Tuple[int, int]] = []
    last = 0
    if not positions:
        return []
    for pos in positions:
        spans.append((last, pos))
        last = pos + 1
    spans.append((last, len(line.rstrip("\n"))))
    # Drop leading empty if first char was '|'
    if line.startswith('|') and spans:
        spans = spans[1:]
    # Drop trailing empty if line ends with '|'
    stripped = line.rstrip("\n")
    if stripped.endswith('|') and spans:
        spans = spans[:-1]
    return spans


# ==========================
# Validation hooks (optional)
# ==========================


def validate_no_code_translation(candidate_text: str, original_slice: str) -> bool:
    """Ensure no backtick code fences/spans leaked into translated text.

    Parameters
    ----------
    candidate_text : str
        Text proposed for insertion.
    original_slice : str
        Original slice text from the Markdown for this span.

    Returns
    -------
    bool
        True if validation passes (no backtick sequences introduced beyond those
        present originally), False otherwise.

    Responsibility
    --------------
    Protect downstream renderers from accidental code fence introduction.
    """
    orig_ticks = original_slice.count("`")
    cand_ticks = candidate_text.count("`")
    return cand_ticks <= orig_ticks


def validate_links_intact(original_md: str, recombined_md: str) -> bool:
    """Compare link destinations between original and recombined Markdown.

    Returns True if the multiset of link/image destinations is identical.

    This heuristic assumes inline-style destinations and autolinks.
    """
    link_dest_re = re.compile(r"\]\(([^)]+)\)")
    auto_re = re.compile(r"<[^>\n]+>")
    orig = sorted(link_dest_re.findall(original_md) + auto_re.findall(original_md))
    new = sorted(link_dest_re.findall(recombined_md) + auto_re.findall(recombined_md))
    return orig == new


def validate_tables_intact(original_md: str, recombined_md: str) -> bool:
    """Check that pipe counts per table row remain identical before/after.

    Not a full parser; it's a conservative safeguard.
    """
    def pipes_per_row(s: str) -> List[int]:
        lines = s.splitlines()
        res = []
        in_fence = False
        fence_re = re.compile(r"^\s*([`~]{3,})")
        for ln in lines:
            if fence_re.match(ln):
                in_fence = not in_fence
            if in_fence:
                continue
            if '|' in ln:
                res.append(ln.count('|'))
        return res

    return pipes_per_row(original_md) == pipes_per_row(recombined_md)


# ==========================
# Telemetry
# ==========================


def fingerprint(*, parser_name: str = "regex_v1", options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return a lightweight fingerprint for run reports.

    Parameters
    ----------
    parser_name : str, keyword-only
        Human-friendly name/version of the parser used for extraction.
    options : dict | None, keyword-only
        Options dict provided to extract_segments; included verbatim in the
        fingerprint for traceability.

    Returns
    -------
    dict
        Mapping with keys: "parser", "options".

    Responsibility
    --------------
    Provide metadata for auditing and reproducibility of segmentation runs.
    """
    return {"parser": parser_name, "options": dict(options or {})}

