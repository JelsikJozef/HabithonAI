import os
from typing import List, Tuple, Dict
from .detectors.regex_detector import RegexDetector
from .token_vault.file_store import FileTokenVault
from .detectors.presidio_detector import PresidioDetector
from ..domain.ports import DetectorPort, TokenVaultPort


def _parse_presidio_langs(spec: str) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            lang, model = part.split(":", 1)
            mapping[lang.strip()] = model.strip()
    return mapping


def build_default() -> Tuple[List[DetectorPort], TokenVaultPort]:
    """Build default detectors and token vault from environment variables.

    Env vars:
    - ANON_VAULT_DIR: directory for file-based vault (default: .anonymization_vault)
    - ANON_DETECTORS: comma-separated detectors to enable (regex, presidio)
    - ANON_PRESIDIO_LANGS: comma-separated lang:model pairs (e.g., "en:en_core_web_sm,es:es_core_news_sm")
    """
    vault_dir = os.getenv("ANON_VAULT_DIR", ".anonymization_vault")
    vault = FileTokenVault(base_dir=vault_dir)

    enabled = {d.strip().lower() for d in os.getenv("ANON_DETECTORS", "regex").split(",")}
    detectors: List[DetectorPort] = []

    if "regex" in enabled:
        detectors.append(RegexDetector())

    if "presidio" in enabled:
        langs_spec = os.getenv("ANON_PRESIDIO_LANGS", "").strip()
        languages = _parse_presidio_langs(langs_spec) if langs_spec else None
        detectors.append(PresidioDetector(languages=languages))

    return detectors, vault
