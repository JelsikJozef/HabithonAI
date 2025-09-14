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
