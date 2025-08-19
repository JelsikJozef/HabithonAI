import os
from typing import List, Tuple, Dict
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
    """Build default Presidio detector and token vault from environment variables.

    Env vars:
    - ANON_VAULT_DIR: directory for file-based vault (default: .anonymization_vault)
    - ANON_PRESIDIO_LANGS: comma-separated lang:model pairs (e.g., "en:en_core_web_sm,de:de_core_news_sm,sk:xx_ent_wiki_sm")
    """
    vault_dir = os.getenv("ANON_VAULT_DIR", ".anonymization_vault")
    vault = FileTokenVault(base_dir=vault_dir)

    langs_spec = os.getenv("ANON_PRESIDIO_LANGS", "").strip()
    languages = _parse_presidio_langs(langs_spec) if langs_spec else None

    detectors: List[DetectorPort] = [PresidioDetector(languages=languages)]
    return detectors, vault
