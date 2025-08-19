from typing import List, Optional, Dict
import importlib
import importlib.util
import logging
import re
import os
import json
from ...domain.entities import PiiEntity
from ...domain.ports import DetectorPort

logger = logging.getLogger(__name__)


class PresidioDetector(DetectorPort):
    """Detector using Microsoft Presidio AnalyzerEngine with resilient fallback.

    - Registers Slovak PatternRecognizers (national ID, postal code, person names).
    - Supports sk/en/de; Slovak defaults to xx_ent_wiki_sm.
    - If Presidio isn't available or analyze fails, falls back to minimal regex (emails/phones/IP/credit cards).
    - Behavior can be adjusted via env:
      * ANON_PRESIDIO_FALLBACK_MODEL: model name to use as fallback when specific model missing.
      * ANON_PRESIDIO_DISABLE_FALLBACK=1: disable regex fallback and raise on failure.
      * ANON_PRESIDIO_PATTERNS: path to JSON with custom recognizers to register.
    """

    name = "presidio"

    # Minimal regex patterns as a resilience fallback (not a separate detector)
    _EMAIL_RE = re.compile(r"(?P<email>[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
    _PHONE_RE = re.compile(r"(?P<phone>(?:\+\d{1,3}[\s-]?)?(?:\(?\d{2,4}\)?[\s-]?)?\d{3,4}[\s-]?\d{3,4})")
    _IPV4_RE = re.compile(r"(?P<ip>\b(?:\d{1,3}\.){3}\d{1,3}\b)")
    _CREDIT_CARD_RE = re.compile(r"(?P<cc>\b(?:\d[ -]*?){13,19}\b)")

    def __init__(self, languages: Optional[Dict[str, str]] = None) -> None:
        self._analyzer = None
        self._init_error: Optional[str] = None
        self._supported_langs: List[str] = []
        self._fallback_lang: str = "en"
        self._disable_regex_fallback: bool = str(os.getenv("ANON_PRESIDIO_DISABLE_FALLBACK", "0")).strip().lower() in {"1", "true", "yes", "on"}
        fallback_model_name = os.getenv("ANON_PRESIDIO_FALLBACK_MODEL", "xx_ent_wiki_sm").strip()
        default_langs: Dict[str, str] = {"en": "en_core_web_sm", "de": "de_core_news_sm", "sk": fallback_model_name or "xx_ent_wiki_sm"}
        requested = dict(default_langs)
        if languages:
            requested.update(languages)
        try:
            analyzer_mod = importlib.import_module("presidio_analyzer")
            AnalyzerEngine = getattr(analyzer_mod, "AnalyzerEngine")
            RecognizerRegistry = getattr(analyzer_mod, "RecognizerRegistry")
            PatternRecognizer = getattr(analyzer_mod, "PatternRecognizer")
            Pattern = getattr(analyzer_mod, "Pattern")
            nlp_mod = importlib.import_module("presidio_analyzer.nlp_engine")
            NlpEngineProvider = getattr(nlp_mod, "NlpEngineProvider")

            effective_models: Dict[str, str] = {}
            disabled_langs: Dict[str, str] = {}

            def _model_available(model_name: str) -> bool:
                try:
                    return importlib.util.find_spec(model_name) is not None
                except Exception:
                    return False

            for lang, model in requested.items():
                if _model_available(model):
                    effective_models[lang] = model
                    continue
                # Try env-configured fallback model
                if fallback_model_name and _model_available(fallback_model_name):
                    effective_models[lang] = fallback_model_name
                    logger.warning("Presidio: model '%s' for lang '%s' not found; falling back to %s", model, lang, fallback_model_name)
                    continue
                # Try default multilingual model
                if model != "xx_ent_wiki_sm" and _model_available("xx_ent_wiki_sm"):
                    effective_models[lang] = "xx_ent_wiki_sm"
                    logger.warning("Presidio: model '%s' for lang '%s' not found; falling back to xx_ent_wiki_sm", model, lang)
                else:
                    disabled_langs[lang] = model
                    logger.warning("Presidio: model '%s' for lang '%s' not found and no fallback available; disabling this language", model, lang)

            self._supported_langs = sorted(effective_models.keys())
            fallback_lang = next((l for l, m in effective_models.items() if m in {fallback_model_name, "xx_ent_wiki_sm"}), None)
            if not fallback_lang and "en" in effective_models:
                fallback_lang = "en"
            if not fallback_lang and self._supported_langs:
                fallback_lang = self._supported_langs[0]
            if fallback_lang:
                self._fallback_lang = fallback_lang

            registry = RecognizerRegistry()
            try:
                registry.load_predefined_recognizers()
            except Exception as e:
                logger.warning("Presidio: failed to load predefined recognizers: %s", e)

            # Custom patterns via JSON file
            patterns_path = os.getenv("ANON_PRESIDIO_PATTERNS")
            if patterns_path:
                try:
                    with open(patterns_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    items = data if isinstance(data, list) else data.get("recognizers", [])
                    for rec in items:
                        try:
                            entity = rec.get("entity") or rec.get("supported_entity")
                            name = rec.get("name") or f"CustomRecognizer_{entity}"
                            lang = rec.get("language") or rec.get("supported_language") or None
                            ctx = rec.get("context") or []
                            pats = rec.get("patterns") or []
                            pat_objs = []
                            for i, p in enumerate(pats):
                                regex = p.get("regex") or p.get("pattern")
                                score = float(p.get("score", 0.5))
                                if not regex:
                                    continue
                                pat_objs.append(Pattern(name=p.get("name", f"pat{i}"), regex=regex, score=score))
                            if entity and pat_objs:
                                registry.add_recognizer(PatternRecognizer(
                                    supported_entity=entity,
                                    name=name,
                                    patterns=pat_objs,
                                    context=ctx,
                                    supported_language=lang,
                                ))
                        except Exception as e:
                            logger.warning("Presidio: failed to register custom recognizer: %s", e)
                except Exception as e:
                    logger.warning("Presidio: failed to load patterns file '%s': %s", patterns_path, e)

            # Slovak recognizers (built-in)
            try:
                rc_pattern = Pattern(name="sk_national_id", regex=r"\b\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\/?\d{3,4}\b", score=0.7)
                registry.add_recognizer(PatternRecognizer(
                    supported_entity="SK_NATIONAL_ID",
                    name="SkNationalIdRecognizer",
                    patterns=[rc_pattern],
                    context=["rodné", "rodne", "číslo", "cislo", "r.č", "r.c", "rc", "rodné číslo", "rodne cislo"],
                    supported_language="sk",
                ))
                psc_pattern = Pattern(name="sk_postal_code", regex=r"\b\d{3}\s?\d{2}\b", score=0.5)
                registry.add_recognizer(PatternRecognizer(
                    supported_entity="SK_POSTAL_CODE",
                    name="SkPostalCodeRecognizer",
                    patterns=[psc_pattern],
                    context=["PSČ", "psc", "poštové", "postove", "smerovacie", "pošta", "posta"],
                    supported_language="sk",
                ))
                # Slovak person name: require a salutation/title to reduce false positives; allow optional academic titles between salutation and name
                name_pattern = Pattern(
                    name="sk_person_name",
                    regex=(
                        r"\b(?:P[áa]n|p[áa]n|Pani|pani|Slečna|slečna|Slecna|slecna)"  # salutations
                        r"\s+(?:(?:Ing\.|Mgr\.|Bc\.|PhDr\.)\s+)*"  # optional titles
                        r"[A-ZÁÄČĎÉÍĹĽŇÓÔŔŠŤÚÝŽ][a-záäčďéíĺľňóôŕšťúýž]+(?:[-\s][A-ZÁÄČĎÉÍĹĽŇÓÔŔŠŤÚÝŽ][a-záäčďéíĺľňóôŕšťúýž]+)+\b"
                    ),
                    score=0.5,
                )
                registry.add_recognizer(PatternRecognizer(
                    supported_entity="PERSON",
                    name="SkPersonNameRecognizer",
                    patterns=[name_pattern],
                    context=["pán", "pan", "pani", "slečna", "slecna", "Ing.", "Mgr.", "Bc.", "PhDr."],
                    supported_language="sk",
                ))
            except Exception as e:
                logger.warning("Presidio: failed adding Slovak recognizers: %s", e)

            # German person names
            try:
                de_person_pat = Pattern(
                    name="de_person_name",
                    regex=(
                        r"\b(?:Herrn?|Hr\.?|Frau|Fr\.?)"  # salutations
                        r"\s+(?:(?:Dr\.|Prof\.)\s+)*"     # optional titles
                        r"[A-ZÄÖÜ][a-zäöüß]+(?:[-\s][A-ZÄÖÜ][a-zäöüß]+)+\b"
                    ),
                    score=0.5,
                )
                registry.add_recognizer(PatternRecognizer(
                    supported_entity="PERSON",
                    name="DePersonNameRecognizer",
                    patterns=[de_person_pat],
                    context=["Herr", "Frau", "Hr.", "Fr.", "Dr.", "Prof."],
                    supported_language="de",
                ))
            except Exception as e:
                logger.warning("Presidio: failed adding German person recognizer: %s", e)

            # Company/organization recognizers (Slovak & German)
            try:
                # Slovak company suffixes: s.r.o., a.s., k.s., v.o.s., o.z., n.o., and spelled forms
                # Company name must consist of capitalized tokens (optionally with &, '-') before the suffix
                sk_org_pat = Pattern(
                    name="sk_company_suffix",
                    regex=(
                        r"(?i)\b"
                        r"(?:[A-ZÁÄČĎÉÍĹĽŇÓÔŔŠŤÚÝŽ][\w'’\-\.áäčďéíĺľňóôŕšťúýžÁÄČĎÉÍĹĽŇÓÔŔŠŤÚÝŽ]*)"
                        r"(?:\s+(?:&|[A-ZÁÄČĎÉÍĹĽŇÓÔŔŠŤÚÝŽ][\w'’\-\.áäčďéíĺľňóôŕšťúýžÁÄČĎÉÍĹĽŇÓÔŔŠŤÚÝŽ]*))*"
                        r"\s+(?:s\.?\s*r\.?\s*o\.?|spol\.?\s*s\s*r\.?\s*o\.?|a\.?\s*s\.?|k\.?\s*s\.?|v\.?\s*o\.?\s*s\.?|o\.?\s*z\.?|n\.?\s*o\.?)\b"
                    ),
                    score=0.5,
                )
                registry.add_recognizer(PatternRecognizer(
                    supported_entity="ORGANIZATION",
                    name="SkCompanyRecognizer",
                    patterns=[sk_org_pat],
                    context=["spoločnosť", "spol.", "firma", "obchodné meno"],
                    supported_language="sk",
                ))
            except Exception as e:
                logger.warning("Presidio: failed adding Slovak company recognizer: %s", e)

            try:
                # German company suffixes: GmbH, GmbH & Co. KG, AG, KG, OHG, UG, GbR, e.V., e.K.
                # Company name must consist of capitalized tokens (optionally with &, Co.) before the suffix
                de_org_pat = Pattern(
                    name="de_company_suffix",
                    regex=(
                        r"\b"
                        r"(?:[A-ZÄÖÜ][\w'’\-\.äöüÄÖÜß]*)"
                        r"(?:\s+(?:&|Co\.|[A-ZÄÖÜ][\w'’\-\.äöüÄÖÜß]*))*"
                        r"\s+(?:GmbH(?:\s*&\s*Co\.?\s*KG)?|AG|KG|OHG|UG|GbR|e\.V\.|e\.K\.)\b"
                    ),
                    score=0.5,
                )
                registry.add_recognizer(PatternRecognizer(
                    supported_entity="ORGANIZATION",
                    name="DeCompanyRecognizer",
                    patterns=[de_org_pat],
                    context=["Firma", "Unternehmen", "Gesellschaft", "AG", "GmbH"],
                    supported_language="de",
                ))
            except Exception as e:
                logger.warning("Presidio: failed adding German company recognizer: %s", e)

            # Initialize NLP engine and analyzer with compatibility across Presidio versions
            if effective_models:
                nlp_conf = {"nlp_engine_name": "spacy", "models": [{"lang_code": l, "model_name": m} for l, m in sorted(effective_models.items())]}
                nlp_engine = None
                try:
                    # Newer Presidio: pass config to constructor, call create_engine()
                    provider = NlpEngineProvider(nlp_configuration=nlp_conf)  # type: ignore[call-arg]
                    try:
                        nlp_engine = provider.create_engine()  # type: ignore[call-arg]
                    except TypeError:
                        # Some variants still expect the config on create_engine
                        nlp_engine = provider.create_engine(nlp_conf)  # type: ignore[arg-type]
                except TypeError:
                    # Older Presidio: init without args, pass config to create_engine
                    provider = NlpEngineProvider()
                    try:
                        nlp_engine = provider.create_engine(nlp_conf)  # type: ignore[arg-type]
                    except TypeError:
                        # Last resort: try no-arg create
                        nlp_engine = provider.create_engine()
                # Align registry/analyzer supported languages to avoid misconfiguration errors
                try:
                    registry.supported_languages = sorted(set(effective_models.keys()))  # type: ignore[attr-defined]
                except Exception:
                    pass
                self._analyzer = AnalyzerEngine(
                    nlp_engine=nlp_engine,
                    supported_languages=list(getattr(registry, "supported_languages", list(effective_models.keys()))),
                    registry=registry,
                )
                logger.info("PresidioDetector initialized with spaCy models: %s", effective_models)
                if disabled_langs:
                    logger.info("PresidioDetector disabled languages (missing models): %s", disabled_langs)
            else:
                self._analyzer = AnalyzerEngine(registry=registry)
                logger.warning("PresidioDetector: no configured spaCy models available; using Presidio default NLP engine (usually English only)")
        except Exception as e:
            self._init_error = str(e)
            self._analyzer = None
            logger.warning("PresidioDetector unavailable: %s", self._init_error)

    def _regex_fallback(self, text: str) -> List[PiiEntity]:
        if self._disable_regex_fallback:
            # When disabled, return no entities to surface lack of Presidio installation in tests/config
            return []
        out: List[PiiEntity] = []
        for m in self._EMAIL_RE.finditer(text):
            s, e = m.span()
            out.append(PiiEntity(type="EMAIL", start=s, end=e, value=m.group(0), score=0.6, detector=self.name))
        for m in self._PHONE_RE.finditer(text):
            s, e = m.span()
            out.append(PiiEntity(type="PHONE", start=s, end=e, value=m.group(0), score=0.5, detector=self.name))
        for m in self._IPV4_RE.finditer(text):
            val = m.group(0)
            octets = val.split(".")
            if any((not o.isdigit()) or int(o) > 255 for o in octets):
                continue
            s, e = m.span()
            out.append(PiiEntity(type="IP", start=s, end=e, value=val, score=0.5, detector=self.name))
        for m in self._CREDIT_CARD_RE.finditer(text):
            digits = re.sub(r"\D", "", m.group(0))
            if 13 <= len(digits) <= 19:
                s, e = m.span()
                out.append(PiiEntity(type="CREDIT_CARD", start=s, end=e, value=m.group(0), score=0.4, detector=self.name))
        return out

    def detect(self, text: str, language: Optional[str] = None) -> List[PiiEntity]:
        if not text:
            return []
        if self._analyzer is None:
            return self._regex_fallback(text)
        requested_lang = (language or "").strip() if language else None
        if requested_lang and (not self._supported_langs or requested_lang in self._supported_langs):
            lang_to_use = requested_lang
        else:
            lang_to_use = self._fallback_lang
        try:
            results = self._analyzer.analyze(text=text, language=lang_to_use)
            out: List[PiiEntity] = []
            for r in results:
                val = text[r.start : r.end]
                out.append(PiiEntity(type=str(r.entity_type), start=int(r.start), end=int(r.end), value=val, score=float(getattr(r, "score", 0.0)) if getattr(r, "score", None) is not None else None, detector=self.name))
            return out
        except Exception:
            # Retry with fallback language when first attempt used a different language
            if requested_lang and lang_to_use != self._fallback_lang:
                try:
                    results = self._analyzer.analyze(text=text, language=self._fallback_lang)
                    out2: List[PiiEntity] = []
                    for r in results:
                        val = text[r.start : r.end]
                        out2.append(PiiEntity(type=str(r.entity_type), start=int(r.start), end=int(r.end), value=val, score=float(getattr(r, "score", 0.0)) if getattr(r, "score", None) is not None else None, detector=self.name))
                    return out2
                except Exception:
                    pass
            # Final safety: regex fallback
            return self._regex_fallback(text)
