from __future__ import annotations
from typing import Dict, Optional, Tuple
import importlib
import importlib.util
import logging

logger = logging.getLogger(__name__)


def _model_available(model_name: str) -> bool:
    try:
        return importlib.util.find_spec(model_name) is not None
    except Exception:
        return False


def resolve_models(requested: Dict[str, str], fallback_model: Optional[str]) -> Tuple[Dict[str, str], Dict[str, str], Optional[str]]:
    """Resolve effective spaCy models per language with fallbacks.

    Parameters
    - requested: Mapping of ISO language codes to desired spaCy model names.
    - fallback_model: Fallback spaCy model name used when a requested model is unavailable.

    Returns
    - effective_models: Dict of language->model that are available and will be used.
    - disabled_langs: Dict of language->requested_model that could not be satisfied.
    - fallback_lang: Preferred fallback language code chosen from effective models.
    """
    effective: Dict[str, str] = {}
    disabled: Dict[str, str] = {}
    fb_lang: Optional[str] = None

    for lang, model in requested.items():
        if _model_available(model):
            effective[lang] = model
            continue
        # Try provided fallback
        if fallback_model and _model_available(fallback_model):
            effective[lang] = fallback_model
            logger.warning("Presidio: model '%s' for lang '%s' not found; falling back to %s", model, lang, fallback_model)
            continue
        # Try default multilingual
        if model != "xx_ent_wiki_sm" and _model_available("xx_ent_wiki_sm"):
            effective[lang] = "xx_ent_wiki_sm"
            logger.warning("Presidio: model '%s' for lang '%s' not found; falling back to xx_ent_wiki_sm", model, lang)
        else:
            disabled[lang] = model
            logger.warning("Presidio: model '%s' for lang '%s' not found and no fallback available; disabling this language", model, lang)

    # choose fallback language preference: any language using the fallback model or English, else first
    fb_lang = next((l for l, m in effective.items() if m in {fallback_model, "xx_ent_wiki_sm"}), None)
    if not fb_lang and "en" in effective:
        fb_lang = "en"
    if not fb_lang and effective:
        fb_lang = sorted(effective.keys())[0]

    return effective, disabled, fb_lang


def build_analyzer(effective_models: Dict[str, str]):
    """Build a Presidio AnalyzerEngine and fresh RecognizerRegistry.

    Parameters
    - effective_models: Mapping of language code to spaCy model name that are available.

    Returns
    - analyzer: Configured AnalyzerEngine instance with spaCy NLP engine and supported languages set.
    - registry: Empty RecognizerRegistry instance for the caller to configure.
    """
    analyzer_mod = importlib.import_module("presidio_analyzer")
    AnalyzerEngine = getattr(analyzer_mod, "AnalyzerEngine")
    RecognizerRegistry = getattr(analyzer_mod, "RecognizerRegistry")
    nlp_mod = importlib.import_module("presidio_analyzer.nlp_engine")
    NlpEngineProvider = getattr(nlp_mod, "NlpEngineProvider")

    registry = RecognizerRegistry()

    if effective_models:
        nlp_conf = {"nlp_engine_name": "spacy", "models": [{"lang_code": l, "model_name": m} for l, m in sorted(effective_models.items())]}
        nlp_engine = None
        try:
            provider = NlpEngineProvider(nlp_configuration=nlp_conf)  # type: ignore[call-arg]
            try:
                nlp_engine = provider.create_engine()  # type: ignore[call-arg]
            except TypeError:
                nlp_engine = provider.create_engine(nlp_conf)  # type: ignore[arg-type]
        except TypeError:
            provider = NlpEngineProvider()
            try:
                nlp_engine = provider.create_engine(nlp_conf)  # type: ignore[arg-type]
            except TypeError:
                nlp_engine = provider.create_engine()
        try:
            registry.supported_languages = sorted(set(effective_models.keys()))  # type: ignore[attr-defined]
        except Exception:
            pass
        analyzer = AnalyzerEngine(
            nlp_engine=nlp_engine,
            supported_languages=list(getattr(registry, "supported_languages", list(effective_models.keys()))),
            registry=registry,
        )
    else:
        analyzer = AnalyzerEngine(registry=registry)
        logger.warning("Presidio: no configured spaCy models available; using Presidio default NLP engine (usually English only)")

    return analyzer, registry
