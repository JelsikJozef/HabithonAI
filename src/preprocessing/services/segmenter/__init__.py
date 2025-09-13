"""Segmenter public API exports.

This package provides a stable API for Markdown segmentation and recombination
used by translation adapters. See api.extract_segments and api.recombine.
"""

from .api import Segment, SegmentPlan, SegmenterOptions, extract_segments, recombine

__all__ = [
    "Segment",
    "SegmentPlan",
    "SegmenterOptions",
    "extract_segments",
    "recombine",
]
