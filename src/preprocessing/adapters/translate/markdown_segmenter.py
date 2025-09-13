# Deprecated shim — do not add new logic here.
from src.preprocessing.services.segmenter.api import (
    extract_segments,
    recombine,
    Segment,
    SegmentPlan,
    SegmenterOptions,
)

__all__ = ["extract_segments", "recombine", "Segment", "SegmentPlan", "SegmenterOptions"]
