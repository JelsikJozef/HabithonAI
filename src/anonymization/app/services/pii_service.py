from __future__ import annotations

import os

from ...adapters.crypto.crypto import Crypto as _Crypto
from ...adapters.detectors.adapter import PresidioDetector
from ...adapters.token_vault.file_store import DEFAULT_VAULT_DIR, FileTokenVault
from ...domain.anonymizer import anonymize as _anonymize
from ...domain.entities import DeAnonymizationResult, DetectionResult, PseudonymizationResult
from ...domain.ports import DetectorPort, TokenVaultPort
from ..config.pii_settings import PiiSettings
from ..denomize import deanonymize as _deanonymize
from ..detect import detect_all
from ..pseudonymize import pseudonymize as _pseudonymize


class PiiService:
    """Application service orchestrating PII langid and anonymization.

    This service centralizes configuration (PiiSettings), composes detectors,
    and manages the token vault for pseudonymization/de-anonymization flows.

    Parameters
    - settings: Optional PiiSettings. If omitted, settings are loaded from environment via PiiSettings.from_env().
    - detectors: Optional list of DetectorPort instances. If omitted, a PresidioDetector is created from settings.
    - vault: Optional TokenVaultPort. If omitted, a FileTokenVault is created at ANON_VAULT_DIR or the default path.
    """

    def __init__(
        self,
        settings: PiiSettings | None = None,
        detectors: list[DetectorPort] | None = None,
        vault: TokenVaultPort | None = None,
    ) -> None:
        self.settings = settings or PiiSettings.from_env()
        self.detectors = detectors or [PresidioDetector(settings=self.settings)]
        if vault is None:
            vault_dir = os.getenv("ANON_VAULT_DIR", DEFAULT_VAULT_DIR)
            self.vault = FileTokenVault(base_dir=vault_dir)
        else:
            self.vault = vault

    def detect(self, text: str, language: str | None = None) -> DetectionResult:
        """Detect PII entities in text using configured detectors.

        Parameters
        - text: Input text to analyze.
        - language: Optional ISO language code hint for detectors (e.g., "en", "de", "sk").

        Returns
        - DetectionResult containing the original text and a merged list of detected entities.
        """
        return detect_all(text, self.detectors, language=language)

    def pseudonymize(
        self, text: str, context_id: str, language: str | None = None
    ) -> PseudonymizationResult:
        """Pseudonymize detected PII with stable tokens and store mappings.

        Parameters
        - text: Input text to pseudonymize.
        - context_id: Identifier for grouping token mappings in the vault (e.g., per document or session).
        - language: Optional ISO language code hint for detectors.

        Returns
        - PseudonymizationResult with original text, pseudonymized text, and token mappings persisted in the vault.
        """
        return _pseudonymize(
            text, self.detectors, self.vault, context_id=context_id, language=language
        )

    def deanonymize(self, anonymized_text: str, context_id: str) -> DeAnonymizationResult:
        """Restore original values by replacing tokens using stored mappings.

        Parameters
        - anonymized_text: Text containing pseudonymization tokens.
        - context_id: Identifier used during pseudonymization to retrieve mappings.

        Returns
        - DeAnonymizationResult with the anonymized input, restored text, and mappings that were applied.
        """
        return _deanonymize(anonymized_text, self.vault, context_id)

    def anonymize(
        self,
        text: str,
        *,
        context_id: str,
        language: str | None = None,
        tenant_id: str | None = None,
    ) -> PseudonymizationResult:
        """Deterministically anonymize text using detectors and crypto hashing.

        - text: Source English text to anonymize.
        - context_id: Mapping namespace (e.g., document ID).
        - language: Optional hint to detectors.
        - tenant_id: Optional tenant scope for hashing.
        """
        crypto = _Crypto()
        return _anonymize(
            text,
            self.detectors,
            crypto,
            self.vault,
            context_id=context_id,
            tenant_id=tenant_id,
            language=language,
        )
