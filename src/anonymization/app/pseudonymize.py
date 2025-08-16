from typing import List, Optional
from uuid import uuid4
from ..domain.entities import DetectionResult, TokenMapping, PseudonymizationResult
from ..domain.ports import DetectorPort, TokenVaultPort
from .detect import detect_all


def _make_token(entity_type: str, index: int) -> str:
    # Use a robust token unlikely to appear naturally in text
    return f"{{{{PII:{entity_type}:{index}:{uuid4().hex[:8]}}}}}"


def pseudonymize(
    text: str,
    detectors: List[DetectorPort],
    vault: TokenVaultPort,
    context_id: str,
    language: Optional[str] = None,
) -> PseudonymizationResult:
    detection = detect_all(text, detectors, language=language)
    entities = detection.entities

    # Build pseudonymized text by replacing spans left-to-right
    out_parts: List[str] = []
    mappings: List[TokenMapping] = []
    cursor = 0
    counters = {}

    for ent in entities:
        out_parts.append(text[cursor:ent.start])
        counters[ent.type] = counters.get(ent.type, 0) + 1
        token = _make_token(ent.type, counters[ent.type])
        out_parts.append(token)
        mappings.append(TokenMapping(token=token, value=ent.value, type=ent.type))
        cursor = ent.end

    out_parts.append(text[cursor:])
    pseudonymized = "".join(out_parts)

    # Save mappings for this context
    vault.save_mappings(context_id, mappings)

    return PseudonymizationResult(
        original_text=text,
        pseudonymized_text=pseudonymized,
        mappings=mappings,
    )

