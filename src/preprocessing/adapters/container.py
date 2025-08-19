from __future__ import annotations

from pathlib import Path
import logging

# Parser registry and parsers
from .parsers import ParserRegistry, TxtParser, PdfParser, DocxParser, MsgParser, ImageParser
# Adapters (ports)
from .dedup import InMemoryDedup
from .quality import QualityChecker
from .enrichment import MetadataEnricher
# Language detector adapter
from .language import FastTextLanguageDetector
# App services
from ..app import (
    ParseService,
    NormalizeService,
    MetadataEnrichmentService,
    DedupService,
    QualityService,
    SerializeService,
    PreprocessPipeline,
)
from ..app.factories import (
    build_serializer_sink,
    build_ocr_service,
    build_anonymization_bridge,
    build_llm_service,
)
from ..settings import Settings

logger = logging.getLogger(__name__)


def build_default_parser_registry() -> ParserRegistry:
    """Backward-compatible helper that returns a default ParserRegistry."""
    img = ImageParser()
    parsers = {
        "txt": TxtParser(),
        "pdf": PdfParser(),
        "docx": DocxParser(),
        "msg": MsgParser(),
        "png": img,
        "jpg": img,
        "jpeg": img,
    }
    return ParserRegistry(parsers)


class Container:
    """Simple factories for default preprocessing assemblies."""

    @staticmethod
    def default_registry() -> ParserRegistry:
        return build_default_parser_registry()

    @staticmethod
    def default_pipeline(
        output_jsonl: Path,
        *,
        settings: Settings,
        enable_ocr: bool = False,
    ) -> PreprocessPipeline:
        # Parsers and parse service
        registry = Container.default_registry()
        parse = ParseService(registry)
        # Normalization and metadata enrichment
        normalize = NormalizeService()
        # Instantiate fastText language detector; fall back gracefully if unavailable
        lang_detector_cb = None
        try:
            detector = FastTextLanguageDetector(
                model_path=settings.language_model_path,
                min_confidence=float(getattr(settings, "language_min_confidence", 0.5)),
            )
            lang_detector_cb = detector.detect
        except Exception as e:
            logger.warning("FastTextLanguageDetector unavailable, falling back to built-in heuristics: %s", e)
        enricher = MetadataEnricher(lang_detector=lang_detector_cb)
        meta = MetadataEnrichmentService(enricher)
        # Dedup and quality
        dedup_store = InMemoryDedup()
        dedup = DedupService(dedup_store)
        quality_adapter = QualityChecker()
        quality = QualityService(quality_adapter)
        # Serializer selection via factory
        sink = build_serializer_sink(output_jsonl)
        serialize = SerializeService(sink)
        # Optional OCR via factory
        ocr_service = build_ocr_service(enable_ocr)
        # Anonymization bridge via factory
        anonymize_bridge = build_anonymization_bridge(settings)
        # LLM service via factory (mandatory)
        llm_service = build_llm_service(settings)
        # Compose pipeline
        return PreprocessPipeline(
            parse,
            normalize,
            meta,
            dedup,
            quality,
            serialize,
            ocr=ocr_service,
            llm=llm_service,
            anonymize=anonymize_bridge,
        )
