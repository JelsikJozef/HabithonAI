from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import os

try:  # optional dependency
    from dotenv import load_dotenv, find_dotenv  # type: ignore
except Exception:  # pragma: no cover
    def load_dotenv(*args, **kwargs):  # type: ignore
        return False
    def find_dotenv(*args, **kwargs):  # type: ignore
        return ""


@dataclass(frozen=True)
class Settings:
    """Application settings loaded once from environment or .env.

    Only this module should load .env. All other modules should receive
    configuration via an instance of this class.
    """

    openai_api_key: str
    openai_model: str = "gpt-4o-mini"
    openai_max_tokens: int = 512
    anonymization_include_text: bool = False
    # Language detection configuration
    language_model_path: Optional[str] = None
    language_min_confidence: float = 0.3
    # Presidio configuration (adapter layer will propagate to anonymization)
    presidio_langs: Optional[str] = None  # e.g., "en:en_core_web_sm,de:de_core_news_sm,sk:xx_ent_wiki_sm"
    presidio_fallback_model: Optional[str] = None  # e.g., "xx_ent_wiki_sm"
    presidio_patterns_path: Optional[str] = None  # path to JSON/YAML with custom recognizers
    presidio_disable_fallback: bool = False  # disable internal regex fallback

    @staticmethod
    def _to_bool(s: Optional[str], default: bool = False) -> bool:
        if s is None:
            return default
        return str(s).strip().lower() in {"1", "true", "yes", "y", "on"}

    @classmethod
    def load(cls, *, use_dotenv: bool = True) -> "Settings":
        """Load settings from the environment, optionally loading .env first.

        - Requires OPENAI_API_KEY; raises if missing.
        - OPENAI_MODEL defaults to 'gpt-4o-mini'.
        - OPENAI_MAX_TOKENS defaults to 512.
        - ANON_INCLUDE_TEXT controls whether to include raw text in outputs alongside pseudonymized text.
        - LANGUAGE_MODEL_PATH optionally points to a local fastText lid.176.ftz file.
        - LANGUAGE_MIN_CONFIDENCE sets the minimum probability to accept a language prediction.
        - ANON_PRESIDIO_LANGS optionally sets Presidio spaCy models per language.
        - ANON_PRESIDIO_FALLBACK_MODEL optionally sets a generic multilingual model name.
        - ANON_PRESIDIO_PATTERNS optionally points to a file with custom recognizers.
        - ANON_PRESIDIO_DISABLE_FALLBACK disables regex fallback in PresidioDetector.
        """
        if use_dotenv:
            try:
                dotenv_path = find_dotenv()
                if dotenv_path:
                    load_dotenv(dotenv_path)
                else:
                    load_dotenv()
            except Exception:
                # Best-effort; continue without .env
                pass
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "OPENAI_API_KEY is required but not set. Set it in your environment or .env file."
            )
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        try:
            max_tokens = int(os.environ.get("OPENAI_MAX_TOKENS", "512"))
        except Exception:
            max_tokens = 512
        include_text = cls._to_bool(os.environ.get("ANON_INCLUDE_TEXT"), default=False)
        # Language detection config
        lang_model_path = os.environ.get("LANGUAGE_MODEL_PATH") or os.environ.get("PREPROCESSING_FASTTEXT_MODEL")
        try:
            lang_min_conf = float(os.environ.get("LANGUAGE_MIN_CONFIDENCE", "0.5"))
        except Exception:
            lang_min_conf = 0.5
        # Clamp to [0, 1]
        if lang_min_conf < 0.0:
            lang_min_conf = 0.0
        if lang_min_conf > 1.0:
            lang_min_conf = 1.0
        # Presidio config reads
        presidio_langs = os.environ.get("ANON_PRESIDIO_LANGS")
        presidio_fallback = os.environ.get("ANON_PRESIDIO_FALLBACK_MODEL")
        presidio_patterns = os.environ.get("ANON_PRESIDIO_PATTERNS")
        presidio_disable_fallback = cls._to_bool(os.environ.get("ANON_PRESIDIO_DISABLE_FALLBACK"), default=False)
        return cls(
            openai_api_key=key,
            openai_model=model,
            openai_max_tokens=max_tokens,
            anonymization_include_text=include_text,
            language_model_path=lang_model_path if lang_model_path else None,
            language_min_confidence=lang_min_conf,
            presidio_langs=presidio_langs if presidio_langs else None,
            presidio_fallback_model=presidio_fallback if presidio_fallback else None,
            presidio_patterns_path=presidio_patterns if presidio_patterns else None,
            presidio_disable_fallback=presidio_disable_fallback,
        )
