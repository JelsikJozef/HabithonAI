from dataclasses import dataclass, field
from typing import Dict, List, Optional
import os


@dataclass
class PiiSettings:
    """Central configuration for PII detection/anonymization.

    This dataclass holds all configurable parameters for the anonymization
    pipeline so they can be controlled from one place (and via environment).

    Attributes
    - language_models: Mapping of ISO language code to spaCy model names for Presidio.
    - fallback_model: spaCy model name used when a requested model is unavailable.
    - disable_regex_fallback: If True, do not use regex fallback when Presidio is unavailable.
    - custom_patterns_path: Optional path to a JSON file with custom Presidio recognizers.
    - enable_predefined_recognizers: Whether to load Presidio's predefined recognizers.
    - person_score_sk: Base confidence score for Slovak PERSON regex recognizer.
    - person_score_de: Base confidence score for German PERSON regex recognizer.
    - person_context_sk: Context words for Slovak PERSON (e.g., salutations/titles) to reduce false positives.
    - person_context_de: Context words for German PERSON to reduce false positives.
    - merge_strategy: Post-merge strategy indicator (informational; domain merge logic enforces actual behavior).
    """
    # Language & models
    language_models: Dict[str, str] = field(default_factory=lambda: {
        "en": "en_core_web_sm",
        "de": "de_core_news_sm",
        "sk": os.getenv("ANON_PRESIDIO_FALLBACK_MODEL", "xx_ent_wiki_sm") or "xx_ent_wiki_sm",
    })
    fallback_model: str = field(default_factory=lambda: os.getenv("ANON_PRESIDIO_FALLBACK_MODEL", "xx_ent_wiki_sm"))
    disable_regex_fallback: bool = field(default_factory=lambda: str(os.getenv("ANON_PRESIDIO_DISABLE_FALLBACK", "0")).strip().lower() in {"1", "true", "yes", "on"})

    # Custom recognizers
    custom_patterns_path: Optional[str] = field(default_factory=lambda: os.getenv("ANON_PRESIDIO_PATTERNS"))
    enable_predefined_recognizers: bool = True

    # Person patterns tuning
    person_score_sk: float = field(default_factory=lambda: float(os.getenv("ANON_PERSON_SCORE_SK", "0.8")))
    person_score_de: float = field(default_factory=lambda: float(os.getenv("ANON_PERSON_SCORE_DE", "0.8")))
    # Restrict contexts to salutations/titles only to reduce false positives
    person_context_sk: List[str] = field(default_factory=lambda: [
        "pán", "pan", "pani", "slečna", "slecna", "Ing.", "Mgr.", "Bc.", "PhDr.",
        "Pán", "Pani", "Slečna",
    ])
    person_context_de: List[str] = field(default_factory=lambda: [
        "Herr", "Frau", "Hr.", "Fr.", "Dr.", "Prof.",
    ])

    # Post-processing
    merge_strategy: str = "length_then_score"  # currently informational; domain merge covers it

    # Labels to ignore from NER models (comma-separated env var)
    ignore_labels: List[str] = field(default_factory=lambda: [lbl.strip() for lbl in os.getenv("ANON_PRESIDIO_IGNORE_LABELS", "").split(",") if lbl.strip()])

    @staticmethod
    def _parse_langs(spec: str) -> Dict[str, str]:
        """Parse a comma-separated spec into a language->model mapping.

        Parameters
        - spec: Comma-separated pairs "lang:model" (e.g., "en:en_core_web_sm,de:de_core_news_sm").

        Returns
        - Dict mapping ISO language codes to spaCy model names.
        """
        mapping: Dict[str, str] = {}
        for part in (spec or "").split(","):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                lang, model = part.split(":", 1)
                mapping[lang.strip()] = model.strip()
        return mapping

    @classmethod
    def from_env(cls) -> "PiiSettings":
        """Create PiiSettings from environment variables.

        Reads known environment variables to override defaults for language models,
        fallback model, regex fallback toggle, and custom recognizer path.

        Returns
        - A populated PiiSettings instance reflecting environment overrides.
        """
        inst = cls()
        # Allow overriding language models via env list
        langs_spec = os.getenv("ANON_PRESIDIO_LANGS", "").strip()
        if langs_spec:
            inst.language_models.update(cls._parse_langs(langs_spec))
        # Keep fallback model consistent if set
        fb = os.getenv("ANON_PRESIDIO_FALLBACK_MODEL")
        if fb:
            inst.fallback_model = fb
            # ensure SK uses fallback if not explicitly overridden
            if "sk" not in inst.language_models:
                inst.language_models["sk"] = fb
        # Custom patterns path
        cpp = os.getenv("ANON_PRESIDIO_PATTERNS")
        if cpp:
            inst.custom_patterns_path = cpp
        # Disable regex fallback toggle handled by field default
        return inst
