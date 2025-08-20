from typing import List, Optional
from ..domain.entities import DetectionResult, merge_overlapping_entities
from ..domain.ports import DetectorPort


def detect_all(text: str, detectors: List[DetectorPort], language: Optional[str] = None) -> DetectionResult:
    """Run all detectors on text and return merged PII entities.

    Parameters
    - text: Input text to analyze.
    - detectors: List of DetectorPort implementations to invoke.
    - language: Optional ISO language hint forwarded to detectors.

    Returns
    - DetectionResult with original text and a de-overlapped, merged list of entities.
    """
    all_entities = []
    for d in detectors:
        try:
            ents = d.detect(text, language=language)
        except Exception:
            ents = []
        all_entities.extend(ents)
    merged = merge_overlapping_entities(all_entities)
    return DetectionResult(text=text, entities=merged)
