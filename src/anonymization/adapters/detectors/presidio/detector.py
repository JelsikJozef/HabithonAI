from __future__ import annotations

import importlib
import logging

from anonymization.app.config.pii_settings import PiiSettings
from anonymization.domain.entities import PiiEntity
from anonymization.domain.ports import DetectorPort

from .engine_factory import build_analyzer, resolve_models
from .fallback_regex.detectors import detect_with_regex
from .language_router import choose_language
from .postprocess.organization import trim_organization_entities
from .postprocess.person import trim_person_entities
from .postprocess.spans import trim_whitespace
from .registry_builder import configure_registry

logger = logging.getLogger(__name__)


class PresidioDetector(DetectorPort):
    """Thin Presidio-based detector adapter.

    Composes a Presidio AnalyzerEngine from PiiSettings, configures a
    RecognizerRegistry (predefined + custom + tuned SK/DE patterns), routes
    language selection, and applies minimal post-processing to spans.
    """

    name = "presidio"

    def __init__(
        self, settings: PiiSettings | None = None, languages: dict[str, str] | None = None
    ) -> None:
        """Create a PresidioDetector from settings.

        Parameters
        - settings: Optional PiiSettings; if None, settings are read from environment via PiiSettings.from_env().
        - languages: Optional override for language_models mapping (ISO code -> spaCy model name).
        """
        # Backward compat: allow explicit languages override
        self.settings = settings or PiiSettings.from_env()
        if languages:
            self.settings.language_models.update(languages)

        self._analyzer = None
        self._supported_langs: list[str] = []
        self._fallback_lang: str | None = None
        self._init_error: str | None = None

        try:
            analyzer_mod = importlib.import_module("presidio_analyzer")
        except Exception as e:  # Presidio not installed
            self._init_error = str(e)
            logger.warning("Presidio unavailable: %s", e)
            return

        try:
            effective, disabled, fb_lang = resolve_models(
                self.settings.language_models, self.settings.fallback_model
            )
            self._supported_langs = sorted(effective.keys())
            self._fallback_lang = fb_lang

            analyzer, registry = build_analyzer(effective)
            # configure registry with built-ins and tuned recognizers
            configure_registry(registry, self.settings, analyzer_mod)

            # swap registry on analyzer (some versions only expose at init; handle gracefully)
            try:
                analyzer.registry = registry  # type: ignore[attr-defined]
            except Exception:
                pass

            self._analyzer = analyzer
            if disabled:
                logger.info("Presidio disabled languages (missing models): %s", disabled)
        except Exception as e:
            self._init_error = str(e)
            logger.warning("PresidioDetector initialization failed: %s", e)
            self._analyzer = None

    def _fallback(self, text: str) -> list[PiiEntity]:
        if self.settings.disable_regex_fallback:
            return []
        return detect_with_regex(text, detector_name=self.name)

    def detect(self, text: str, language: str | None = None) -> list[PiiEntity]:
        """Detect PII entities in text.

        Parameters
        - text: Input text to analyze.
        - language: Optional ISO language hint (e.g., "en", "de", "sk"); if not supported, a fallback language is used.

        Returns
        - List of PiiEntity spans with type, offsets, value, score (if available), and detector name.
        """
        if not text:
            return []
        if self._analyzer is None:
            return self._fallback(text)

        lang = choose_language(language, self._supported_langs, self._fallback_lang)

        try:
            results = self._analyzer.analyze(text=text, language=lang)
        except Exception:
            # try fallback language once
            if lang != self._fallback_lang and self._fallback_lang:
                try:
                    results = self._analyzer.analyze(text=text, language=self._fallback_lang)
                except Exception:
                    return self._fallback(text)
            else:
                return self._fallback(text)

        out: list[PiiEntity] = []
        for r in results:
            val = text[r.start : r.end]
            score = (
                float(getattr(r, "score", 0.0)) if getattr(r, "score", None) is not None else None
            )
            out.append(
                PiiEntity(
                    type=str(r.entity_type),
                    start=int(r.start),
                    end=int(r.end),
                    value=val,
                    score=score,
                    detector=self.name,
                )
            )

        # Post-process spans
        out = trim_whitespace(out, text)
        out = trim_person_entities(out, text, lang)
        out = trim_organization_entities(out, text, lang)
        return out
