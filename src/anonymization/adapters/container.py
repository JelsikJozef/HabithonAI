import os

from ..app.config.pii_settings import PiiSettings
from ..domain.ports import DetectorPort, TokenVaultPort
from .detectors.adapter import PresidioDetector
from .token_vault.file_store import FileTokenVault


def _parse_presidio_langs(spec: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            lang, model = part.split(":", 1)
            mapping[lang.strip()] = model.strip()
    return mapping


def build_default() -> tuple[list[DetectorPort], TokenVaultPort]:
    """Construct default detectors and token vault based on environment.

    Environment variables
    - ANON_VAULT_DIR: Directory for file-based token vault (default: src/anonymization/.anonymization_vault).
    - ANON_PRESIDIO_LANGS: Comma-separated "lang:model" pairs (e.g., "en:en_core_web_sm,de:de_core_news_sm").
    - ANON_PRESIDIO_FALLBACK_MODEL, ANON_PRESIDIO_DISABLE_FALLBACK, ANON_PRESIDIO_PATTERNS,
      ANON_PERSON_SCORE_SK/DE: Additional settings consumed via PiiSettings.from_env().

    Returns
    - detectors: List containing a PresidioDetector built with centralized settings.
    - vault: A TokenVaultPort implementation (file-based) for storing token mappings.
    """
    vault_dir = os.getenv("ANON_VAULT_DIR", "src/anonymization/.anonymization_vault")
    vault = FileTokenVault(base_dir=vault_dir)

    # Central settings
    settings = PiiSettings.from_env()

    # Backward-compatible explicit languages override if provided
    langs_spec = os.getenv("ANON_PRESIDIO_LANGS", "").strip()
    languages = _parse_presidio_langs(langs_spec) if langs_spec else None

    detectors: list[DetectorPort] = [PresidioDetector(settings=settings, languages=languages)]
    return detectors, vault
