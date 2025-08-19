from typing import List, Optional, Dict
import importlib
import importlib.util
import logging
from ...domain.entities import PiiEntity
from ...domain.errors import DetectionError
from ...domain.ports import DetectorPort

logger = logging.getLogger(__name__)


class PresidioDetector(DetectorPort):
    """Detector using Microsoft Presidio AnalyzerEngine.

    Notes:
    - Uses importlib to load Presidio at runtime to avoid hard import errors when not installed.
    - If Presidio/spaCy or language models are missing, detect() will raise DetectionError
      with a helpful message.
    - You can provide a languages mapping to control spaCy models per lang code, e.g.:
      {"en": "en_core_web_sm", "es": "es_core_news_sm"}
    - If languages is None, a sensible multilingual default is attempted with fallbacks.
    """

    name = "presidio"

    def __init__(self, languages: Optional[Dict[str, str]] = None) -> None:
        self._analyzer = None
        self._init_error: Optional[str] = None
        # Default multilingual mapping (can be overridden via constructor/env)
        default_langs: Dict[str, str] = {
            "en": "en_core_web_sm",
            "de": "de_core_news_sm",
            "cs": "cs_core_news_sm",
            "hu": "hu_core_news_sm",
            # spaCy has no official Slovak model; use multilingual as a best-effort fallback
            "sk": "xx_ent_wiki_sm",
        }
        requested = dict(languages) if languages else default_langs
        try:
            analyzer_mod = importlib.import_module("presidio_analyzer")
            AnalyzerEngine = getattr(analyzer_mod, "AnalyzerEngine")
            nlp_mod = importlib.import_module("presidio_analyzer.nlp_engine")
            NlpEngineProvider = getattr(nlp_mod, "NlpEngineProvider")
            provider = NlpEngineProvider()

            effective_models: Dict[str, str] = {}
            disabled_langs: Dict[str, str] = {}

            def _model_available(model_name: str) -> bool:
                try:
                    return importlib.util.find_spec(model_name) is not None
                except Exception:
                    return False

            # Build effective mapping with fallbacks
            for lang, model in requested.items():
                if _model_available(model):
                    effective_models[lang] = model
                    continue
                # Fallback to multilingual model if present
                if model != "xx_ent_wiki_sm" and _model_available("xx_ent_wiki_sm"):
                    effective_models[lang] = "xx_ent_wiki_sm"
                    logger.warning("Presidio: model '%s' for lang '%s' not found; falling back to xx_ent_wiki_sm", model, lang)
                else:
                    disabled_langs[lang] = model
                    logger.warning("Presidio: model '%s' for lang '%s' not found and no fallback available; disabling this language", model, lang)

            # If none effective, let Presidio create a default engine (likely English only)
            if effective_models:
                nlp_conf = {
                    "nlp_engine_name": "spacy",
                    "models": [
                        {"lang_code": l, "model_name": m} for l, m in sorted(effective_models.items())
                    ],
                }
                nlp_engine = provider.create_engine(nlp_conf)
                self._analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=list(effective_models.keys()))
                logger.info("PresidioDetector initialized with spaCy models: %s", effective_models)
                if disabled_langs:
                    logger.info("PresidioDetector disabled languages (missing models): %s", disabled_langs)
            else:
                self._analyzer = AnalyzerEngine()
                logger.warning(
                    "PresidioDetector: no configured spaCy models available; using Presidio default NLP engine (usually English only)"
                )
        except Exception as e:  # Broad to capture ImportError and model issues
            self._init_error = str(e)
            self._analyzer = None
            logger.warning("PresidioDetector unavailable: %s", self._init_error)

    def detect(self, text: str, language: Optional[str] = None) -> List[PiiEntity]:
        if not text:
            return []
        if self._analyzer is None:
            raise DetectionError(
                "PresidioDetector is unavailable. Please install presidio-analyzer and spaCy, "
                "and download appropriate language models. Init error: " + (self._init_error or "unknown")
            )
        lang = language or "en"
        try:
            results = self._analyzer.analyze(text=text, language=lang)
        except Exception as e:
            raise DetectionError(f"Presidio analyze failed: {e}")
        out: List[PiiEntity] = []
        for r in results:
            value = text[r.start : r.end]
            out.append(
                PiiEntity(
                    type=str(r.entity_type),
                    start=int(r.start),
                    end=int(r.end),
                    value=value,
                    score=float(getattr(r, "score", 0.0)) if getattr(r, "score", None) is not None else None,
                    detector=self.name,
                )
            )
        return out
