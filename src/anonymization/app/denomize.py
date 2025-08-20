from typing import List
from ..domain.entities import DeAnonymizationResult, TokenMapping
from ..domain.ports import TokenVaultPort


def deanonymize(anonymized_text: str, vault: TokenVaultPort, context_id: str) -> DeAnonymizationResult:
    """Restore original text by replacing tokens using stored mappings.

    Parameters
    - anonymized_text: Text containing pseudonymization tokens to be resolved.
    - vault: TokenVaultPort used to retrieve token->value mappings.
    - context_id: Identifier used during pseudonymization to fetch the correct mappings set.

    Returns
    - DeAnonymizationResult with the anonymized input, fully restored text, and the mappings applied in the process.
    """
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
