from __future__ import annotations

from typing import Any, Iterable

from ..domain.models import RawDocument, ParsedDocument
from .parse import ParseService
from .normalize import NormalizeService
from .enrich_metadata import MetadataEnrichmentService
from .deduplicate import DedupService
from .quality import QualityService
from .serialize import SerializeService
from .ocr import OcrService
from .llm_enrich import LlmEnrichmentService


class PreprocessPipeline:
    """Compose preprocessing steps into a single use-case.

    Steps: parse → (maybe_ocr) → normalize → meta → dedup → quality → (llm) → serialize
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
    ) -> None:
        self._parse = parse
        self._normalize = normalize
        self._meta = meta
        self._dedup = dedup
        self._quality = quality
        self._serialize = serialize
        self._ocr = ocr
        self._llm = llm

    def process_one(
        self,
        raw: RawDocument,
        *,
        languages: tuple[str, ...] = ("sk", "en"),
        do_llm: bool = False,
    ) -> dict[str, Any]:
        # parse
        doc: ParsedDocument = self._parse.parse_one(raw)
        # maybe OCR
        if self._ocr is not None:
            doc = self._ocr.maybe_ocr(doc, languages)
        # normalize
        doc = self._normalize.normalize(doc)
        # metadata enrichment
        doc = self._meta.run(doc)
        # deduplicate
        is_dup = False
        if doc.hash:
            is_dup = self._dedup.check_or_remember(doc.hash)
        if is_dup:
            return {"ok": False, "skipped": True, "reasons": ["duplicate"]}
        # quality
        qres = self._quality.evaluate(doc)
        ok = bool(qres.get("ok", False))
        reasons = list(qres.get("reasons", []))
        # optional LLM enrichment
        if ok and do_llm and self._llm is not None:
            doc = self._llm.summarize(doc)
            doc = self._llm.keywords(doc)
        # serialize only if quality passed
        if ok:
            self._serialize.append(doc)
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
            except Exception:
                stats["failed"] += 1
                continue
            if res.get("skipped"):
                stats["skipped"] += 1
                if "duplicate" in res.get("reasons", []):
                    stats["duplicate"] += 1
            if res.get("ok"):
                stats["processed"] += 1
        return stats
