from __future__ import annotations

import json
import logging

from ..presidio.engine_factory import build_analyzer  # noqa: F401  # only for types in editors

logger = logging.getLogger(__name__)


def configure_registry(registry, settings, analyzer_mod) -> None:
    """Configure RecognizerRegistry with predefined, custom, and tuned SK/DE recognizers.

    Parameters
    - registry: Presidio RecognizerRegistry instance to configure.
    - settings: PiiSettings providing flags, paths, and tuned scores/contexts.
    - analyzer_mod: Imported presidio_analyzer module to access Pattern and PatternRecognizer.

    Returns
    - None. The provided registry is modified in place.
    """
    PatternRecognizer = getattr(analyzer_mod, "PatternRecognizer")
    Pattern = getattr(analyzer_mod, "Pattern")

    # Load predefined recognizers if enabled
    try:
        if getattr(settings, "enable_predefined_recognizers", True):
            registry.load_predefined_recognizers()
    except Exception as e:
        logger.warning("Presidio: failed to load predefined recognizers: %s", e)

    # Custom patterns via JSON path
    if getattr(settings, "custom_patterns_path", None):
        try:
            with open(settings.custom_patterns_path, encoding="utf-8") as f:
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
                        pat_objs.append(
                            Pattern(name=p.get("name", f"pat{i}"), regex=regex, score=score)
                        )
                    if entity and pat_objs:
                        registry.add_recognizer(
                            PatternRecognizer(
                                supported_entity=entity,
                                name=name,
                                patterns=pat_objs,
                                context=ctx,
                                supported_language=lang,
                            )
                        )
                except Exception as e:
                    logger.warning("Presidio: failed to register custom recognizer: %s", e)
        except Exception as e:
            logger.warning(
                "Presidio: failed to load patterns file '%s': %s", settings.custom_patterns_path, e
            )

    # Slovak specific recognizers
    try:
        # Slovak national ID
        registry.add_recognizer(
            PatternRecognizer(
                supported_entity="SK_NATIONAL_ID",
                name="SkNationalIdRecognizer",
                patterns=[
                    Pattern(
                        name="sk_national_id",
                        regex=r"\b\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\/?\d{3,4}\b",
                        score=0.7,
                    )
                ],
                context=[
                    "rodné",
                    "rodne",
                    "číslo",
                    "cislo",
                    "r.č",
                    "r.c",
                    "rc",
                    "rodné číslo",
                    "rodne cislo",
                ],
                supported_language="sk",
            )
        )
        # Slovak postal code
        registry.add_recognizer(
            PatternRecognizer(
                supported_entity="SK_POSTAL_CODE",
                name="SkPostalCodeRecognizer",
                patterns=[Pattern(name="sk_postal_code", regex=r"\b\d{3}\s?\d{2}\b", score=0.5)],
                context=["PSČ", "psc", "poštové", "postove", "smerovacie", "pošta", "posta"],
                supported_language="sk",
            )
        )
        # Slovak person name anchored to salutations/titles; raised score
        sk_person_regex = (
            r"\b(?:P[áa]n|p[áa]n|Pani|pani|Slečna|slečna|Slecna|slecna)"  # salutation
            r"\s+(?:(?:Ing\.|Mgr\.|Bc\.|PhDr\.|MUDr\.|JUDr\.|RNDr\.)\s+)*"  # optional titles
            r"[A-ZÁÄČĎÉÍĹĽŇÓÔŔŠŤÚÝŽ][a-záäčďéíĺľňóôŕšťúýž]+"  # first name
            r"(?:[-\s][A-ZÁÄČĎÉÍĹĽŇÓÔŔŠŤÚÝŽ][a-záäčďéíĺľňóôŕšťúýž]+)+\b"  # last (and possibly middle) names
        )
        registry.add_recognizer(
            PatternRecognizer(
                supported_entity="PERSON",
                name="SkPersonNameRecognizer",
                patterns=[
                    Pattern(
                        name="sk_person_name",
                        regex=sk_person_regex,
                        score=float(getattr(settings, "person_score_sk", 0.8)),
                    )
                ],
                context=list(getattr(settings, "person_context_sk", [])),
                supported_language="sk",
            )
        )
    except Exception as e:
        logger.warning("Presidio: failed adding Slovak recognizers: %s", e)

    # German specific recognizers
    try:
        # German company recognizer
        de_org_regex = (
            r"\b(?:[A-ZÄÖÜ][\w'’\-\.äöüÄÖÜß]*)"  # leading token
            r"(?:\s+(?:&|Co\.|[A-ZÄÖÜ][\w'’\-\.äöüÄÖÜß]*))*"  # additional tokens
            r"\s+(?:GmbH(?:\s*&\s*Co\.?\s*KG)?|AG|KG|OHG|UG|GbR|e\.V\.|e\.K\.)\b"
        )
        registry.add_recognizer(
            PatternRecognizer(
                supported_entity="ORGANIZATION",
                name="DeCompanyRecognizer",
                patterns=[Pattern(name="de_company_suffix", regex=de_org_regex, score=0.6)],
                context=["Firma", "Unternehmen", "Gesellschaft", "AG", "GmbH"],
                supported_language="de",
            )
        )
        # German person name anchored to salutations/titles; raised score
        de_person_regex = (
            r"\b(?:Herrn?|Hr\.?|Frau|Fr\.?)"  # salutation
            r"\s+(?:(?:Dr\.|Prof\.|Dipl\.-Ing\.)\s+)*"  # optional titles
            r"[A-ZÄÖÜ][a-zäöüß]+(?:[-\s][A-ZÄÖÜ][a-zäöüß]+)+\b"
        )
        registry.add_recognizer(
            PatternRecognizer(
                supported_entity="PERSON",
                name="DePersonNameRecognizer",
                patterns=[
                    Pattern(
                        name="de_person_name",
                        regex=de_person_regex,
                        score=float(getattr(settings, "person_score_de", 0.8)),
                    )
                ],
                context=list(getattr(settings, "person_context_de", [])),
                supported_language="de",
            )
        )
    except Exception as e:
        logger.warning("Presidio: failed adding German recognizers: %s", e)
