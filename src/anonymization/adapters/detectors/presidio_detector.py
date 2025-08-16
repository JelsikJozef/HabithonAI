from typing import List, Optional, Dict
import importlib
from ...domain.entities import PiiEntity
from ...domain.errors import DetectionError
from ...domain.ports import DetectorPort


class PresidioDetector(DetectorPort):
    """Detector using Microsoft Presidio AnalyzerEngine.

    Notes:
    - Uses importlib to load Presidio at runtime to avoid hard import errors when not installed.
    - If Presidio/spaCy or language models are missing, detect() will raise DetectionError
      with a helpful message.
    - You can provide a languages mapping to control spaCy models per lang code, e.g.:
      {"en": "en_core_web_sm", "es": "es_core_news_sm"}
    """

    name = "presidio"

    def __init__(self, languages: Optional[Dict[str, str]] = None) -> None:
        self._analyzer = None
        self._init_error: Optional[str] = None
        try:
            analyzer_mod = importlib.import_module("presidio_analyzer")
            AnalyzerEngine = getattr(analyzer_mod, "AnalyzerEngine")
            if languages:
                nlp_mod = importlib.import_module("presidio_analyzer.nlp_engine")
                NlpEngineProvider = getattr(nlp_mod, "NlpEngineProvider")
                provider = NlpEngineProvider()
                nlp_conf = {
                    "nlp_engine_name": "spacy",
                    "models": [
                        {"lang_code": lang, "model_name": model}
                        for lang, model in languages.items()
                    ],
                }
                nlp_engine = provider.create_engine(nlp_conf)
                self._analyzer = AnalyzerEngine(nlp_engine=nlp_engine)
            else:
                self._analyzer = AnalyzerEngine()
        except Exception as e:  # Broad to capture ImportError and model issues
            self._init_error = str(e)
            self._analyzer = None

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
