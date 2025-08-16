from typing import List
from ..domain.entities import DeAnonymizationResult, TokenMapping
from ..domain.ports import TokenVaultPort


def deanonymize(anonymized_text: str, vault: TokenVaultPort, context_id: str) -> DeAnonymizationResult:
    mappings: List[TokenMapping] = vault.get_mappings(context_id)
    restored = anonymized_text
    # Replace tokens with their original values; replace longest tokens first for safety
    for m in sorted(mappings, key=lambda x: len(x.token), reverse=True):
        restored = restored.replace(m.token, m.value)
    return DeAnonymizationResult(
        anonymized_text=anonymized_text,
        restored_text=restored,
        mappings_used=mappings,
    )

