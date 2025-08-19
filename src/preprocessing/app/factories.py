from __future__ import annotations

from pathlib import Path
import hashlib
import logging
from typing import Optional

# Adapters (ports)
from preprocessing.adapters.serializer import JsonlSerializer
from preprocessing.adapters.serializer.per_file_jsonl import PerFileJsonlSerializer
from preprocessing.adapters.ocr import PdfOcr
from preprocessing.adapters.enrichment.LLM.llm_openai_enricher import OpenAiLlmEnricher

# App services
from .llm_enrich import LlmEnrichmentService
from .llm_anonymize import LlmAnonymisationService

# Domain
from ..domain.ports import SerializerPort
# Integration
from ..anonymization_integration import AnonymizationBridge
from ..settings import Settings

logger = logging.getLogger(__name__)


# Serializer selection factory

def build_serializer_sink(output_jsonl: Path) -> SerializerPort:
    """Choose serializer based on output path: file (json/jsonl) vs directory.

    Returns a SerializerPort implementation.
    """
    try:
        if output_jsonl.exists() and output_jsonl.is_dir():
            logger.info("Serializer: per-file JSONL in directory %s", output_jsonl)
            return PerFileJsonlSerializer(output_jsonl)
        # If suffix indicates jsonl/json, use single file serializer
        if output_jsonl.suffix.lower() in {".jsonl", ".json"}:
            logger.info("Serializer: single JSONL file %s", output_jsonl)
            return JsonlSerializer(output_jsonl)
        # Default to per-file when path has no .jsonl/.json suffix
        logger.info("Serializer: per-file JSONL in directory %s", output_jsonl)
        return PerFileJsonlSerializer(output_jsonl)
    except Exception as e:
        logger.warning("Serializer selection failed: %s; falling back to single-file JSONL", e)
        return JsonlSerializer(output_jsonl)


def build_ocr_service(enable_ocr: bool):
    """Build optional OCR service when enabled.

    Returns an instance of OcrService or None.
    """
    if not enable_ocr:
        return None
    from .ocr import OcrService  # local to avoid cycles in typing

    ocr_port = PdfOcr("tesseract")
    logger.info("OCR enabled (tesseract)")
    return OcrService(ocr_port)


# Anonymization bridge factory

def build_anonymization_bridge(settings: Settings) -> Optional[AnonymizationBridge]:
    """Create AnonymizationBridge using anonymization package if available.

    Returns None when anonymization is not available.
    """
    try:
        from anonymization.adapters.container import build_default as _build_anon
        from anonymization.app.detect import detect_all as _detect_all
        from anonymization.app.pseudonymize import pseudonymize as _pseudonymize
        from anonymization.domain.entities import TokenMapping as _TokenMapping

        detectors, vault = _build_anon()

        def _detect(text: str, language: str | None = None):
            return _detect_all(text, detectors, language=language)

        def _ctx_id_for(text: str) -> str:
            return "doc:" + hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()

        class _PseudoWrapper:
            """Wrapper to adapt pseudonymization to the bridge interface."""

            def run(
                self,
                *,
                text: str,
                language: str | None = None,
                context_id: str | None = None,
                entities: list[dict] | None = None,
            ):
                ctx = context_id or _ctx_id_for(text)
                # If entities provided, build pseudonymized text and mappings without re-running detection
                if entities:
                    # Sort by start, then replace left-to-right
                    ents = sorted(
                        [
                            {
                                "type": e.get("type"),
                                "start": int(e.get("start", 0)),
                                "end": int(e.get("end", 0)),
                                "value": e.get("value", ""),
                            }
                            for e in entities
                            if isinstance(e, dict)
                        ],
                        key=lambda x: x["start"],
                    )
                    out_parts = []
                    mappings = []
                    cursor = 0
                    counters = {}
                    for ent in ents:
                        s = ent["start"]
                        epos = ent["end"]
                        if s < cursor:
                            # overlapping or unsorted; skip to avoid corrupt output
                            continue
                        out_parts.append(text[cursor:s])
                        t = ent["type"]
                        counters[t] = counters.get(t, 0) + 1
                        token = "{{PII:%s:%d:%s}}" % (
                            t,
                            counters[t],
                            hashlib.sha256((t + str(counters[t])).encode()).hexdigest()[:8],
                        )
                        out_parts.append(token)
                        mappings.append({"token": token, "value": ent["value"], "type": t})
                        cursor = epos
                    out_parts.append(text[cursor:])
                    pseudonymized = "".join(out_parts)
                    vault.save_mappings(
                        ctx,
                        [
                            _TokenMapping(token=m["token"], value=m["value"], type=m["type"]) for m in mappings
                        ],
                    )
                    return {"pseudonymized_text": pseudonymized, "mappings": mappings, "context_id": ctx}
                # Fallback: run full pseudonymize (will do detection internally)
                return _pseudonymize(text, detectors, vault, ctx, language=language)

        bridge = AnonymizationBridge(
            _detect,
            _PseudoWrapper(),
            policy={"include_text": bool(settings.anonymization_include_text)},
        )
        logger.info("Anonymization bridge enabled (regex/presidio per env)")
        return bridge
    except Exception as e:
        logger.warning("Anonymization bridge unavailable: %s", e)
        return None


# LLM service factory

def build_llm_service(settings: Settings) -> LlmEnrichmentService:
    """Instantiate the LLM enrichment service using OpenAI and anonymization service.

    Raises RuntimeError when initialization fails.
    """
    try:
        # Instantiate detectors and vault once for anonymization service
        from anonymization.adapters.container import build_default as _build_anon2

        detectors2, vault2 = _build_anon2()
        anonymizer = LlmAnonymisationService(detectors2, vault2)

        # OpenAI client (prefer test stub path, fallback to real path)
        try:
            from preprocessing.adapters.enrichment.openai_client import OpenAiClient as _OpenAiClient  # type: ignore
        except Exception:
            from preprocessing.adapters.enrichment.LLM.openai_client import OpenAiClient as _OpenAiClient  # type: ignore
        llm_client = _OpenAiClient(api_key=settings.openai_api_key)
        model = settings.openai_model
        # Use compatibility wrapper that supports both JSON chat and direct method stubs
        llm_port = OpenAiLlmEnricher(
            llm_client, model=model, max_tokens=int(settings.openai_max_tokens)
        )
        return LlmEnrichmentService(llm_port, anonymizer=anonymizer)
    except Exception as e:
        raise RuntimeError(
            "Failed to initialize LLM enrichment: %s. Ensure OPENAI_API_KEY is valid and openai package is installed." % (e,)
        )
