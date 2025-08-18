from __future__ import annotations

from typing import Any, Iterable
import logging

from ..domain.models import RawDocument, ParsedDocument
from .parse import ParseService
from .normalize import NormalizeService
from .enrich_metadata import MetadataEnrichmentService
from .deduplicate import DedupService
from .quality import QualityService
from .serialize import SerializeService
from .ocr import OcrService
from .llm_enrich import LlmEnrichmentService
from ..anonymization_integration import AnonymizationBridge

logger = logging.getLogger(__name__)


class PreprocessPipeline:
    """Compose preprocessing steps into a single use-case.

    Steps: parse → (maybe_ocr) → normalize → meta → dedup → quality → (anonymize) → (llm) → serialize
    """

    def __init__(
        self,
        parse: ParseService,
        normalize: NormalizeService,
        meta: MetadataEnrichmentService,
        dedup: DedupService,
        quality: QualityService,
        serialize: SerializeService,
        ocr: OcrService | None = None,
        llm: LlmEnrichmentService | None = None,
        anonymize: AnonymizationBridge | None = None,
    ) -> None:
        self._parse = parse
        self._normalize = normalize
        self._meta = meta
        self._dedup = dedup
        self._quality = quality
        self._serialize = serialize
        self._ocr = ocr
        self._llm = llm
        self._anon = anonymize

    def process_one(
        self,
        raw: RawDocument,
        *,
        languages: tuple[str, ...] = ("sk", "en"),
        do_llm: bool = False,  # ignored; kept for backward-compat
    ) -> dict[str, Any]:
        # parse
        doc: ParsedDocument = self._parse.parse_one(raw)
        logger.info("STEP parse: ext=%s len=%d", raw.ext, len(doc.text or ""))
        # maybe OCR
        if self._ocr is not None:
            before_len = len(doc.text or "")
            doc = self._ocr.maybe_ocr(doc, languages)
            logger.info("STEP ocr: before=%d after=%d", before_len, len(doc.text or ""))
        # normalize
        doc = self._normalize.normalize(doc)
        logger.info("STEP normalize: len=%d", len(doc.text or ""))
        # metadata enrichment
        doc = self._meta.run(doc)
        logger.info("STEP enrich: lang=%s hash=%s tokens=%s", doc.language, (doc.hash or "")[:8], doc.tokens)
        # deduplicate
        is_dup = False
        if doc.hash:
            is_dup = self._dedup.check_or_remember(doc.hash)
        logger.info("STEP dedup: duplicate=%s", is_dup)
        if is_dup:
            return {"ok": False, "skipped": True, "reasons": ["duplicate"]}
        # quality
        qres = self._quality.evaluate(doc)
        ok = bool(qres.get("ok", False))
        reasons = list(qres.get("reasons", []))
        logger.info("STEP quality: ok=%s reasons=%s", ok, reasons)
        # anonymization (optional) before LLM
        if ok and self._anon is not None:
            before_meta = dict(doc.metadata)
            doc = self._anon.run(doc)
            pii_count = int(doc.metadata.get("pii_count", 0)) if isinstance(doc.metadata, dict) else 0
            logger.info("STEP anonymize: pii_count=%d", pii_count)
        # LLM enrichment: always run if configured (ignore do_llm)
        if ok and self._llm is not None:
            doc = self._llm.summarize(doc)
            doc = self._llm.keywords(doc)
            meta = doc.metadata or {}
            logger.info(
                "STEP llm: summary_len=%d tags=%d",
                len((meta.get("summary") or "")),
                len(meta.get("tags") or []),
            )
        # serialize only if quality passed
        if ok:
            self._serialize.append(doc)
            logger.info("STEP serialize: done")
        return {"ok": ok, "skipped": not ok, "reasons": reasons}

    def process_many(self, raws: Iterable[RawDocument], **kwargs: Any) -> dict[str, Any]:
        stats = {
            "total": 0,
            "processed": 0,
            "skipped": 0,
            "duplicate": 0,
            "failed": 0,
        }
        for r in raws:
            stats["total"] += 1
            try:
                res = self.process_one(r, **kwargs)
            except Exception as e:
                stats["failed"] += 1
                try:
                    logger.exception("STEP error: failed processing %s", getattr(r, "path", r))
                except Exception:
                    logger.exception("STEP error: failed processing item")
                continue
            if res.get("skipped"):
                stats["skipped"] += 1
                if "duplicate" in res.get("reasons", []):
                    stats["duplicate"] += 1
            if res.get("ok"):
                stats["processed"] += 1
        return stats
