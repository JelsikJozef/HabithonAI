from uuid import uuid4

from ..domain.entities import PseudonymizationResult, TokenMapping
from ..domain.ports import DetectorPort, TokenVaultPort
from .detect import detect_all


def _make_token(entity_type: str, index: int) -> str:
    # Use a robust token unlikely to appear naturally in text
    return f"{{{{PII:{entity_type}:{index}:{uuid4().hex[:8]}}}}}"


def _map_token_type(entity_type: str) -> str:
    # Map incoming entity types to desired token categories
    mapping = {
        "PERSON": "NAME",
        "ORGANIZATION": "COMPANY",
    }
    return mapping.get(entity_type, entity_type)


def pseudonymize(
    text: str,
    detectors: list[DetectorPort],
    vault: TokenVaultPort,
    context_id: str,
    language: str | None = None,
) -> PseudonymizationResult:
    """Replace detected PII with stable tokens and persist mappings.

    Parameters
    - text: Input text to pseudonymize.
    - detectors: List of DetectorPort instances used to detect PII.
    - vault: TokenVaultPort used to store token->value mappings for later restoration.
    - context_id: Identifier for namespacing mappings (e.g., per document or session).
    - language: Optional ISO language hint forwarded to detectors.

    Returns
    - PseudonymizationResult including original text, pseudonymized text, and the list of TokenMapping objects saved to the vault.
    """
    detection = detect_all(text, detectors, language=language)
    entities = detection.entities

    # Build pseudonymized text by replacing spans left-to-right
    out_parts: list[str] = []
    mappings: list[TokenMapping] = []
    cursor = 0
    counters = {}

    for ent in entities:
        out_parts.append(text[cursor : ent.start])
        token_type = _map_token_type(ent.type)
        counters[token_type] = counters.get(token_type, 0) + 1
        token = _make_token(token_type, counters[token_type])
        out_parts.append(token)
        # store original type for reference; deanonymize uses the token string for replacement
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
