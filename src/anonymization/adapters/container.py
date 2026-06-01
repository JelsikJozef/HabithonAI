import os

from ..app.config.pii_settings import PiiSettings
from ..domain.ports import DetectorPort, TokenVaultPort
from .token_vault.file_store import DEFAULT_VAULT_DIR, FileTokenVault
from .token_vault.postgres_store import PostgresTokenVault
from .detectors.regex_detector import RegexDetector


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
    - ANON_VAULT_DIR: Directory for file-based token vault (default: outputs/anonymization_vault).
    - ANON_PRESIDIO_LANGS: Comma-separated "lang:model" pairs (e.g., "en:en_core_web_sm,de:de_core_news_sm").
    - ANON_PRESIDIO_FALLBACK_MODEL, ANON_PRESIDIO_DISABLE_FALLBACK, ANON_PRESIDIO_PATTERNS,
      ANON_PERSON_SCORE_SK/DE: Additional settings consumed via PiiSettings.from_env().

    Returns
    - detectors: List containing a PresidioDetector built with centralized settings.
    - vault: A TokenVaultPort implementation (file-based) for storing token mappings.
    """
    # Choose token vault: Postgres if DSN is provided, else file-based
    pg_dsn = os.getenv("ANON_POSTGRES_DSN")
    if pg_dsn:
        vault = PostgresTokenVault(dsn=pg_dsn)
        vault.ensure_schema()
    else:
        vault_dir = os.getenv("ANON_VAULT_DIR", DEFAULT_VAULT_DIR)
        vault = FileTokenVault(base_dir=vault_dir)

    # Central settings
    settings = PiiSettings.from_env()

    # Detector selection
    detector_spec = os.getenv("ANON_DETECTORS", "presidio").strip().lower()
    detectors: list[DetectorPort] = []
    want_regex_only = detector_spec == "regex"
    if want_regex_only:
        detectors = [RegexDetector()]
    else:
        # Try Presidio; fallback to regex if unavailable
        try:
            from .detectors.adapter import PresidioDetector  # lazy import

            langs_spec = os.getenv("ANON_PRESIDIO_LANGS", "").strip()
            languages = _parse_presidio_langs(langs_spec) if langs_spec else None
            detectors = [PresidioDetector(settings=settings, languages=languages)]
        except Exception:
            detectors = [RegexDetector()]
    return detectors, vault
