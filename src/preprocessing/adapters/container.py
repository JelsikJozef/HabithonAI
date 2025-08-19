from __future__ import annotations

from pathlib import Path
import hashlib
import os
import logging

try:
    from dotenv import load_dotenv, find_dotenv  # type: ignore
except Exception:  # pragma: no cover
    def load_dotenv(*args, **kwargs):  # type: ignore
        return False
    def find_dotenv(*args, **kwargs):  # type: ignore
        return ""

# Parser registry and parsers
from .parsers import ParserRegistry, TxtParser, PdfParser, DocxParser, MsgParser, ImageParser
# Adapters (ports)
from .serializer import JsonlSerializer
from .serializer.per_file_jsonl import PerFileJsonlSerializer
from .dedup import InMemoryDedup
from .quality import QualityChecker
from .enrichment import MetadataEnricher
from .enrichment.llm_openai_enricher import OpenAiLlmEnricher
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
from ..app.llm_anonymize import LlmAnonymisationService
from ..anonymization_integration import AnonymizationBridge

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
        enable_ocr: bool = False,
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
        # Serializer selection (file vs directory for 1→1 mapping)
        try:
            if output_jsonl.exists() and output_jsonl.is_dir():
                sink = PerFileJsonlSerializer(output_jsonl)
                logger.info("Serializer: per-file JSONL in directory %s", output_jsonl)
            else:
                if output_jsonl.suffix.lower() in {".jsonl", ".json"}:
                    sink = JsonlSerializer(output_jsonl)
                    logger.info("Serializer: single JSONL file %s", output_jsonl)
                else:
                    sink = PerFileJsonlSerializer(output_jsonl)
                    logger.info("Serializer: per-file JSONL in directory %s", output_jsonl)
        except Exception as e:
            logger.warning("Serializer selection failed: %s; falling back to single-file JSONL", e)
            sink = JsonlSerializer(output_jsonl)
        serialize = SerializeService(sink)
        # Optional OCR
        ocr_service = None
        if enable_ocr:
            ocr_port = PdfOcr("tesseract")
            from ..app.ocr import OcrService  # local import to avoid cycles in typing
            ocr_service = OcrService(ocr_port)
            logger.info("OCR enabled (tesseract)")
        # Anonymization bridge between normalize and LLM
        anonymize_bridge = None
        try:
            from anonymization.adapters.container import build_default as _build_anon
            from anonymization.app.detect import detect_all as _detect_all
            from anonymization.app.pseudonymize import pseudonymize as _pseudonymize
            detectors, vault = _build_anon()

            def _detect(text: str, language: str | None = None):
                return _detect_all(text, detectors, language=language)

            def _ctx_id_for(text: str) -> str:
                return "doc:" + hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()

            class _PseudoWrapper:
                def run(self, *, text: str, language: str | None = None):
                    ctx = _ctx_id_for(text)
                    return _pseudonymize(text, detectors, vault, ctx, language=language)

            anonymize_bridge = AnonymizationBridge(_detect, _PseudoWrapper(), policy={"include_text": False})
            logger.info("Anonymization bridge enabled (regex/presidio per env)")
        except Exception as e:
            logger.warning("Anonymization bridge unavailable: %s", e)
            anonymize_bridge = None
        # LLM (mandatory)
        dotenv_path = find_dotenv()
        if dotenv_path:
            load_dotenv(dotenv_path)
        else:
            load_dotenv()
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is required but not set. Set it in your environment or .env file to run preprocessing."
            )
        try:
            from .enrichment.openai_client import OpenAiClient  # lazy import
            # Instantiate detectors and vault once for anonymization service
            from anonymization.adapters.container import build_default as _build_anon2
            detectors2, vault2 = _build_anon2()
            anonymizer = LlmAnonymisationService(detectors2, vault2)
            # Real OpenAI enricher that does a single-shot call
            llm_client = OpenAiClient(api_key=api_key)
            model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            llm_port = OpenAiLlmEnricher(llm_client, model=model, max_tokens=int(os.getenv("OPENAI_MAX_TOKENS", "512")))
            from ..app.llm_enrich import LlmEnrichmentService as _LlmSvc
            llm_service = _LlmSvc(llm_port, anonymizer=anonymizer)
            logger.info("LLM enabled (OpenAI model=%s)", model)
        except Exception as e:
            raise RuntimeError(
                "Failed to initialize LLM enrichment: %s. Ensure OPENAI_API_KEY is valid and openai package is installed." % (e,)
            )
        # Compose pipeline
        return PreprocessPipeline(parse, normalize, meta, dedup, quality, serialize, ocr=ocr_service, llm=llm_service, anonymize=anonymize_bridge)
