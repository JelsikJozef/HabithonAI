"""
Deterministic anonymization service in the domain layer.
"""
from typing import List

from .entities import TokenMapping, PseudonymizationResult, PiiEntity
from .entities import merge_overlapping_entities
from .ports import DetectorPort, Crypto, TokenVaultPort
from .errors import AnonymizationError


def anonymize(
    text: str,
    detectors: List[DetectorPort],
    crypto: Crypto,
    vault: TokenVaultPort,
    context_id: str,
    tenant_id: str | None = None,
    language: str | None = None,
) -> PseudonymizationResult:
    """
    Detect PII deterministically and replace with hash tokens, persisting mappings.

    - text: input text
    - detectors: list of PII detector implementations
    - crypto: deterministic crypto adapter (hash)
    - vault: token vault for saving mappings
    - context_id: namespace for mappings (e.g., document ID)
    - tenant_id: optional tenant scope for hashing and vault
    - language: optional language hint for detectors
    """
    try:
        # run all detectors and merge overlapping spans
        entities: List[PiiEntity] = []
        for det in detectors:
            entities.extend(det.detect(text, language))
        entities = merge_overlapping_entities(entities)

        # build anonymized text
        out_parts: List[str] = []
        mappings: List[TokenMapping] = []
        cursor = 0
        for ent in entities:
            out_parts.append(text[cursor : ent.start])
            # deterministic hash token
            token = crypto.hash(ent.value, tenant_id=tenant_id)
            out_parts.append(token)
            mappings.append(TokenMapping(token=token, value=ent.value, type=ent.type))
            cursor = ent.end
        out_parts.append(text[cursor:])
        anonymized = "".join(out_parts)

        # persist mappings
        vault.save_mappings(context_id, mappings)

        return PseudonymizationResult(
            original_text=text,
            pseudonymized_text=anonymized,
            mappings=mappings,
        )
    except Exception as e:
        raise AnonymizationError(f"Anonymization failed: {e}") from e
