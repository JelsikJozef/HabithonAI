# Deprecated shim — backward-compatibility for translate adapters.
from __future__ import annotations

from typing import Mapping, Any, Iterable, Tuple

from src.preprocessing.services.segmenter.api import (
    extract_segments as _extract_segments_api,
    recombine as _recombine_api,
    Segment as _CoreSegment,
    SegmentPlan,
    SegmenterOptions,
)


class SegmentationError(Exception):
    """Backward-compatible exception for segmentation failures."""


class IdMismatchError(Exception):
    """Raised when translated ids do not match extracted ids (legacy API)."""


class RecombinationError(Exception):
    """Backward-compatible recombine failure type expected by adapters."""


class Segment(_CoreSegment):  # type: ignore[misc]
    """Compat Segment exposing .part and .order for tests/legacy callers.

    Inherits the frozen dataclass from the core API and adds computed helpers:
    - part: Optional[int] parsed from id suffix '#pN'. None when full segment.
    - order: Stable order index based on extraction ordering.
    """

    # __init__ inherited; we add properties via methods to keep dataclass frozen
    @property
    def part(self) -> int | None:
        sid = self.id
        h = sid.rfind("#p")
        if h != -1:
            try:
                return int(sid[h + 2 :])
            except Exception:
                return None
        return None

    # order is injected during wrapping; default to -1 if not provided
    __order_index: int = -1  # private storage

    @property
    def order(self) -> int:
        return getattr(self, "_Segment__order_index", -1)

    # Small helper to set order on the frozen instance via object.__setattr__
    def _with_order(self, idx: int) -> "Segment":
        object.__setattr__(self, "_Segment__order_index", idx)
        return self


def _wrap_segments(core_segments: Iterable[_CoreSegment]) -> list[Segment]:
    wrapped: list[Segment] = []
    for i, s in enumerate(core_segments):
        seg = Segment(id=s.id, start=s.start, end=s.end, text=s.text, kind=s.kind)._with_order(i)
        wrapped.append(seg)
    return wrapped


def extract_segments(
    md_text: str, options: Mapping[str, Any] | SegmenterOptions | None = None
) -> Tuple[list[Segment], SegmentPlan]:
    """Compat wrapper to call the new API with legacy option names.

    Accepts either a SegmenterOptions instance (passes through) or a dict-like
    mapping with legacy keys used by adapters, mapping them onto the new API.
    Returns a list of compat Segment objects exposing .part and .order.
    """
    try:
        if isinstance(options, SegmenterOptions) or options is None:
            seg_opts = options if isinstance(options, SegmenterOptions) else SegmenterOptions()
        else:
            # Map legacy keys to new API
            o = dict(options)
            seg_opts = SegmenterOptions(
                max_segment_chars=int(
                    o.get("segment_max_chars", o.get("max_segment_chars", 2000)) or 2000
                ),
                translate_tables=bool(
                    o.get("translate_table_cells", o.get("translate_tables", True))
                ),
                translate_link_labels=bool(
                    o.get("translate_link_label", o.get("translate_link_labels", True))
                ),
            )
        core_segs, plan = _extract_segments_api(md_text, options=seg_opts)  # type: ignore[arg-type]
        return _wrap_segments(core_segs), plan
    except (TypeError, ValueError) as exc:
        raise SegmentationError(str(exc))


def recombine(
    md_text: str,
    translated: Mapping[str, str] | list[dict[str, str]],
    plan: SegmentPlan,
    options: Mapping[str, Any] | None = None,
) -> str:
    """Compat recombine accepting list of {id,text} or mapping and optional options.

    Maps list input to a mapping expected by the core API and ignores options.
    Raises IdMismatchError on id set mismatch for compatibility with tests.
    """
    try:
        if isinstance(translated, list):
            mapping = {e["id"]: e["text"] for e in translated}
        else:
            mapping = dict(translated)
        return _recombine_api(md_text=md_text, translated=mapping, plan=plan)
    except ValueError as exc:
        msg = str(exc)
        if msg.startswith("Translated segments do not match extracted set"):
            raise IdMismatchError(msg)
        raise


__all__ = [
    "extract_segments",
    "recombine",
    "Segment",
    "SegmentPlan",
    "SegmenterOptions",
    "SegmentationError",
    "IdMismatchError",
    "RecombinationError",
]
