from __future__ import annotations

from pathlib import Path

# Parser registry and parsers
from .parsers import ParserRegistry, TxtParser, PdfParser, DocxParser, MsgParser, ImageParser
# Adapters (ports)
from .serializer import JsonlSerializer
from .dedup import InMemoryDedup
from .quality import QualityChecker
from .enrichment import MetadataEnricher, LlmEnricher
from .ocr import PdfOcr
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
        enable_ocr: bool = False,
        enable_llm: bool = False,
    ) -> PreprocessPipeline:
        # Parsers and parse service
        registry = Container.default_registry()
        parse = ParseService(registry)
        # Normalization and metadata enrichment
        normalize = NormalizeService()
        enricher = MetadataEnricher()
        meta = MetadataEnrichmentService(enricher)
        # Dedup and quality
        dedup_store = InMemoryDedup()
        dedup = DedupService(dedup_store)
        quality_adapter = QualityChecker()
        quality = QualityService(quality_adapter)
        # Serializer
        sink = JsonlSerializer(output_jsonl)
        serialize = SerializeService(sink)
        # Optional OCR
        ocr_service = None
        if enable_ocr:
            ocr_port = PdfOcr("tesseract")
            from ..app.ocr import OcrService  # local import to avoid cycles in typing
            ocr_service = OcrService(ocr_port)
        # Optional LLM
        llm_service = None
        if enable_llm:
            # Minimal dummy client unless user provides a real one; returns empty text
            class _DummyClient:
                def complete(self, *, prompt: str, model: str, max_tokens: int = 512):  # noqa: D401
                    return ""
            llm_port = LlmEnricher(_DummyClient(), model="dummy")
            from ..app.llm_enrich import LlmEnrichmentService as _LlmSvc
            llm_service = _LlmSvc(llm_port)
        # Compose pipeline
        return PreprocessPipeline(parse, normalize, meta, dedup, quality, serialize, ocr=ocr_service, llm=llm_service)
