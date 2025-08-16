from typing import List, Optional
from ..domain.entities import DetectionResult, merge_overlapping_entities
from ..domain.ports import DetectorPort


def detect_all(text: str, detectors: List[DetectorPort], language: Optional[str] = None) -> DetectionResult:
    """Run all detectors and merge overlapping entities."""
    all_entities = []
    for d in detectors:
        try:
            ents = d.detect(text, language=language)
        except Exception:
            ents = []
        all_entities.extend(ents)
    merged = merge_overlapping_entities(all_entities)
    return DetectionResult(text=text, entities=merged)

