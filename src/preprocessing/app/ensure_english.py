from __future__ import annotations

"""English variant orchestrator (coordination only, no heavy I/O).

Responsibilities
================
- Decide whether a MarkdownDoc needs translate to English and with what options.
- Coordinate language langid and translate ports.
- Enforce invariants (UTF-8/LF, variant/lang values, determinism hints).
- Delegate path planning and writing to the injected writer port.
- Provide batch orchestration with bounded parallelism and deterministic ordering.

This module does not implement language langid or translate itself.
It converts adapter/vendor failures into domain-level results and avoids
raising vendor exceptions.
"""

import os
import time
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol, TypedDict, runtime_checkable

from preprocessing.domain.errors import (
    SettingsError,
    TranslationError,
    WriteError,
)
from preprocessing.domain.models_markdown import MarkdownDoc, validate_markdown_doc
from preprocessing.domain.ports import (
    CachePort,
    GlossaryPort,
    LanguageDetectError,
    LanguageDetectPort,
    MarkdownSerializerPort,
    TargetPaths,
    TranslatePort,
    WriterContext,
)
from preprocessing.domain.ports import EnglishDetectPort, SimilarityPort
from preprocessing.settings_translation import TRANSLATION as _TR_SETTINGS

# -----------------------------
# Public typed shapes
# -----------------------------


class EnsureEnglishResult(TypedDict, total=False):
    """Result record for a single document orchestration.

    Keys:
        status: "created" | "skipped_exists" | "skipped_already_en" | "failed".
        src_lang: Detected or declared source language code (e.g., "sk", "de", "en").
        tgt_lang: Always "en" for this use-case.
        written_path: Absolute path of the English Markdown file if created (or planned in dry-run).
        meta: JSON-serializable metadata (engine fingerprint, timings, counts, cache/glossary hints).
        error: Optional short error descriptor {code, message, details?} when status=="failed".
    """

    status: str
    src_lang: str | None
    tgt_lang: str
    written_path: str | None
    meta: dict[str, Any]
    error: dict[str, Any] | None


class BatchEnglishResult(TypedDict, total=False):
    """Aggregate result for a batch orchestration.

    Keys:
        total: Number of input documents.
        created: Count of successfully created English variants.
        skipped_exists: Count skipped due to existing outputs with overwrite=False.
        skipped_already_en: Count skipped because source is already English.
        failed: Count of failed documents.
        results: Per-document EnsureEnglishResult in deterministic input order.
        wall_millis: End-to-end wall clock time in milliseconds for the whole batch.
        engines: Optional aggregate engine counters or last-seen capabilities.
    """

    total: int
    created: int
    skipped_exists: int
    skipped_already_en: int
    failed: int
    results: list[EnsureEnglishResult]
    wall_millis: float
    engines: dict[str, Any]


class EnglishCfg(TypedDict, total=False):
    """Configuration for English variant orchestration.

    Keys (single-doc and batch):
        tgt_lang: Target language code. Defaults to "en".
        style: "natural" | "literal". Advice for the translate engine.
        glossary_id: Optional glossary identifier string.
        max_segment_chars: Optional int upper bound for adapter segmentation.
        strict: If True, fail fast on non-critical issues (e.g., unexpected variant/lang).
        overwrite: If False and output exists, skip without writing.
        dry_run: If True, plan only, no writes.
        copy_when_already_en: If True, copy/write an English variant for already-English sources when absent.
        en_confidence_threshold: Float in [0,1] to treat detector outcome as English. Default 0.95.
        workers: Parallel workers for batch. Default 1.
        on_error: "skip" | "fail_fast" policy for batch.
        writer_ctx: WriterContext instance used by the writer for path planning/writing.
    """

    tgt_lang: str
    style: str
    glossary_id: str | None
    max_segment_chars: int | None
    strict: bool
    overwrite: bool
    dry_run: bool
    copy_when_already_en: bool
    en_confidence_threshold: float
    workers: int
    on_error: str
    writer_ctx: WriterContext


@runtime_checkable
class _PortsBundle(Protocol):
    """Minimal ports this orchestrator expects.

    Attributes:
        langid: LanguageDetectPort implementation.
        translate: TranslatePort implementation.
        writer: MarkdownSerializerPort implementation.
        glossary: Optional GlossaryPort, if your translate adapter delegates to it or exposes it.
        cache: Optional CachePort, if your translate adapter exposes it.
        writer_ctx: Optional WriterContext on the bundle for convenience (alternative to cfg.writer_ctx).
        english_detector: Optional EnglishDetectPort for validation and probing.
        similarity: Optional SimilarityPort for near-identity detection.
        translate_secondary: Optional secondary TranslatePort used for deterministic fallback.
    """

    langid: LanguageDetectPort
    translate: TranslatePort
    writer: MarkdownSerializerPort
    glossary: GlossaryPort | None
    cache: CachePort | None
    writer_ctx: WriterContext | None
    english_detector: EnglishDetectPort | None
    similarity: SimilarityPort | None
    translate_secondary: TranslatePort | None


# -----------------------------
# Internal helpers
# -----------------------------


def _cfg_get(cfg: Mapping[str, Any] | object, key: str, default: Any = None) -> Any:
    if isinstance(cfg, Mapping):
        return cfg.get(key, default)
    return getattr(cfg, key, default)


def _resolve_writer_ctx(ports: _PortsBundle, cfg: Mapping[str, Any] | object) -> WriterContext:
    ctx = _cfg_get(cfg, "writer_ctx", None)
    if ctx is None and hasattr(ports, "writer_ctx"):
        ctx = getattr(ports, "writer_ctx")
    if ctx is None:
        raise SettingsError("writer context (writer_ctx) is required for path planning/writing")
    return ctx  # type: ignore[return-value]


def _compute_target_paths(
    writer: MarkdownSerializerPort, ctx: WriterContext, doc: MarkdownDoc
) -> TargetPaths:
    # Plan-only; may raise WriteError which the caller will handle.
    return writer.compute_paths(doc, ctx)


# -----------------------------
# Public API: single document
# -----------------------------


def ensure_english_variant(
    doc: MarkdownDoc,
    ports: _PortsBundle,
    cfg: EnglishCfg | Mapping[str, Any] | object,
    context: dict[str, Any] | None = None,
) -> EnsureEnglishResult:
    """Create (or reuse) the English variant for one Markdown document.

    Parameters:
        doc: MarkdownDoc assumed to be the original variant (source language) produced by the convert-only phase.
            Invariants expected on entry: UTF-8 encoding, LF newlines, valid Markdown, stable doc_id and path.
        ports: Bundle with active implementations:
            - langid: LanguageDetectPort
            - translate: TranslatePort
            - writer: MarkdownSerializerPort
            - glossary: Optional GlossaryPort (not directly used here)
            - cache: Optional CachePort (not directly used here)
            - writer_ctx: Optional WriterContext (alternative to cfg.writer_ctx)
            - english_detector: Optional EnglishDetectPort used for validation and probe selection
            - similarity: Optional SimilarityPort to detect near-identity outputs
            - translate_secondary: Optional secondary TranslatePort for fallback
        cfg: Read-only configuration dict/dataclass controlling behavior:
            - tgt_lang: Target language (default "en").
            - style: "natural" | "literal" (advice to translate engine).
            - glossary_id: String or None.
            - max_segment_chars: Per-segment size upper bound.
            - strict: Bool, fail fast on non-critical issues (default False).
            - overwrite: Bool, whether to overwrite existing EN files.
            - dry_run: Bool, plan only, no writes.
            - copy_when_already_en: Bool, whether to write/copy when source is English and output missing (default True).
            - en_confidence_threshold: Float [0,1] to treat detected English as already English (default 0.95).
            - writer_ctx: WriterContext for the writer; required unless provided via ports.
            - routing: Optional dict overriding settings_translation.TRANSLATION["routing"].
        context: Optional dict with trace data (run_id, doc_id, src_path_rel). Safe to ignore by adapters.

    Returns:
        EnsureEnglishResult: Lightweight record with fields:
            - status: "created" | "skipped_exists" | "skipped_already_en" | "failed".
            - src_lang: Detected or declared source language code.
            - tgt_lang: "en".
            - written_path: Absolute path to the English Markdown file when created or planned (dry-run).
            - meta: Dict with engine fingerprint, timings (millis), segment counts, cache hits, glossary id etc.
            - error: Short error descriptor when failed, never vendor-specific.

    Error policy:
        This function never propagates vendor-specific exceptions. It may raise a domain SettingsError
        on programming misuse (e.g., missing writer context). All other failures are returned as
        status="failed" with an error payload.
    """

    t0 = time.perf_counter()

    # Derived configuration with defaults
    tgt_lang = (_cfg_get(cfg, "tgt_lang", "en") or "en").lower()
    style = _cfg_get(cfg, "style", "natural")
    glossary_id = _cfg_get(cfg, "glossary_id", None)
    max_segment_chars = _cfg_get(cfg, "max_segment_chars", None)
    strict = bool(_cfg_get(cfg, "strict", False))
    overwrite = bool(_cfg_get(cfg, "overwrite", False))
    dry_run = bool(_cfg_get(cfg, "dry_run", False))
    copy_when_already_en = bool(_cfg_get(cfg, "copy_when_already_en", True))
    en_threshold = float(_cfg_get(cfg, "en_confidence_threshold", 0.95))
    routing_cfg = dict(getattr(cfg, "routing", None) or _cfg_get(cfg, "routing", {})) or dict(
        _TR_SETTINGS.get("routing", {})
    )
    tau_low = float(routing_cfg.get("tau_low", 0.70))
    delta_close = float(routing_cfg.get("delta_close", 0.05))
    tau_en = float(routing_cfg.get("tau_en", 0.90))
    sim_noop = float(routing_cfg.get("similarity_noop_threshold", 0.92))
    probe_k = int(routing_cfg.get("probe", {}).get("k", 3))
    probe_slice = int(routing_cfg.get("probe", {}).get("slice_chars", 600))
    max_retries = int(routing_cfg.get("max_retries", 3))
    cand_scope = list(routing_cfg.get("candidates", []) or [])

    # Resolve writer context early (used for existence check and planning/writing)
    writer_ctx = _resolve_writer_ctx(ports, cfg)

    # Pre-checks and planning: compute deterministic target path using a derived English doc
    planned_doc = doc.copy_with(variant="english", lang=tgt_lang)
    try:
        target_paths = _compute_target_paths(ports.writer, writer_ctx, planned_doc)
    except WriteError as e:
        # Planning failure: treat as failed
        return {
            "status": "failed",
            "src_lang": (doc.lang or None),
            "tgt_lang": tgt_lang,
            "written_path": None,
            "meta": {"timings": {"detect_ms": 0.0, "translate_ms": 0.0, "write_ms": 0.0}},
            "error": e.to_dict(),
        }

    out_md_path = target_paths["out_md_path"]

    # Short-circuit if output exists and overwrite=False (plan-only still returns path)
    if not overwrite and os.path.exists(out_md_path):
        return {
            "status": "skipped_exists",
            "src_lang": (doc.lang or None),
            "tgt_lang": tgt_lang,
            "written_path": out_md_path,
            "meta": {"timings": {"detect_ms": 0.0, "translate_ms": 0.0, "write_ms": 0.0}},
        }

    # Language decision (with optional top-k)
    detect_ms = 0.0
    src_lang: str | None = doc.lang.lower() if isinstance(doc.lang, str) else None
    detected_conf: float | None = None
    topk_info: list[dict[str, Any]] = []
    detect_flags: dict[str, bool] = {}
    if not src_lang:
        try:
            t_detect = time.perf_counter()
            # Prefer top-k when available
            if hasattr(ports.langid, "detect_topk"):
                res = ports.langid.detect_topk(
                    doc.text_md,
                    k=5,
                    hints={
                        "candidates": cand_scope or None,
                        "doc_id": doc.doc_id,
                        "path": doc.path,
                    },
                    context=context,
                )
                src_lang = res.get("lang_code", None)
                detected_conf = res.get("confidence", None)
                topk_info = list(res.get("topk", []))
                detect_flags = dict(res.get("flags", {}))
            else:
                src_lang, detected_conf = ports.langid.detect(
                    doc.text_md,
                    hints={
                        "doc_id": doc.doc_id,
                        "path": doc.path,
                        "candidates": cand_scope or None,
                    },
                    context=context,
                )
            detect_ms = (time.perf_counter() - t_detect) * 1000.0
            src_lang = (src_lang or "").lower() or None
        except LanguageDetectError as e:
            return {
                "status": "failed",
                "src_lang": None,
                "tgt_lang": tgt_lang,
                "written_path": None,
                "meta": {"timings": {"detect_ms": detect_ms, "translate_ms": 0.0, "write_ms": 0.0}},
                "error": e.to_dict(),
            }

    # Skip path for English
    if (
        (src_lang == "en")
        or (src_lang is not None and src_lang.lower() == "en")
        or (src_lang is None and detected_conf is not None and detected_conf >= en_threshold)
        or (src_lang == "en" and (detected_conf is None or detected_conf >= en_threshold))
    ):
        # Plan-only is still useful to return the path. Optionally write/copy if not present.
        if dry_run:
            return {
                "status": "skipped_already_en",
                "src_lang": src_lang or "en",
                "tgt_lang": tgt_lang,
                "written_path": out_md_path,
                "meta": {
                    "reason": "already_en",
                    "timings": {"detect_ms": detect_ms, "translate_ms": 0.0, "write_ms": 0.0},
                    "routing": {"topk": topk_info, "flags": detect_flags},
                },
            }

        if os.path.exists(out_md_path) and not overwrite:
            return {
                "status": "skipped_exists",
                "src_lang": src_lang or "en",
                "tgt_lang": tgt_lang,
                "written_path": out_md_path,
                "meta": {
                    "reason": "already_en",
                    "timings": {"detect_ms": detect_ms, "translate_ms": 0.0, "write_ms": 0.0},
                    "routing": {"topk": topk_info, "flags": detect_flags},
                },
            }

        if copy_when_already_en:
            # Write a copy with variant set to english
            english_doc = doc.copy_with(variant="english", lang="en")
            try:
                t_write = time.perf_counter()
                wr = ports.writer.write(english_doc, writer_ctx)
                write_ms = (time.perf_counter() - t_write) * 1000.0
                return {
                    "status": "skipped_already_en",
                    "src_lang": src_lang or "en",
                    "tgt_lang": tgt_lang,
                    "written_path": wr.get("out_md_path", out_md_path),
                    "meta": {
                        "reason": "already_en",
                        "writer": {
                            "status": wr.get("status"),
                            "sidecar_written": wr.get("sidecar_written", False),
                        },
                        "timings": {
                            "detect_ms": detect_ms,
                            "translate_ms": 0.0,
                            "write_ms": write_ms,
                        },
                        "routing": {"topk": topk_info, "flags": detect_flags},
                    },
                }
            except WriteError as e:
                return {
                    "status": "failed",
                    "src_lang": src_lang or "en",
                    "tgt_lang": tgt_lang,
                    "written_path": None,
                    "meta": {
                        "timings": {"detect_ms": detect_ms, "translate_ms": 0.0, "write_ms": 0.0}
                    },
                    "error": e.to_dict(),
                }
        # Not copying, just report plan
        return {
            "status": "skipped_already_en",
            "src_lang": src_lang or "en",
            "tgt_lang": tgt_lang,
            "written_path": out_md_path,
            "meta": {
                "reason": "already_en",
                "timings": {"detect_ms": detect_ms, "translate_ms": 0.0, "write_ms": 0.0},
                "routing": {"topk": topk_info, "flags": detect_flags},
            },
        }

    # Translation path (may use advanced routing if english_detector & similarity present)
    options: dict[str, Any] = {
        "style": style,
        "glossary_id": glossary_id,
        "max_segment_chars": max_segment_chars,
        "strict": strict,
    }
    translate_ms = 0.0
    write_ms = 0.0

    # Advanced routing candidates
    def _gather_candidates() -> list[str]:
        if topk_info:
            return [c["code"].lower() for c in topk_info if isinstance(c.get("code"), str)]
        return [src_lang] if src_lang else []

    def _probe_choose(codes: list[str]) -> str:
        # Run micro-probe across codes using english_detector + similarity
        if not hasattr(ports, "english_detector") or not hasattr(ports, "similarity"):
            return codes[0] if codes else (src_lang or "auto")
        en_det = getattr(ports, "english_detector", None)
        sim = getattr(ports, "similarity", None)
        if not en_det or not sim:
            return codes[0] if codes else (src_lang or "auto")
        # Prepare slice
        txt = (doc.text_md or "").strip()
        if len(txt) > probe_slice:
            head = txt[: probe_slice // 2]
            tail = txt[-(probe_slice - len(head)) :]
            sample = head + "\n" + tail
        else:
            sample = txt
        best_code = None
        best_score = -1.0
        tried = 0
        for code in codes:
            if code == "en":
                continue
            if tried >= probe_k:
                break
            tried += 1
            try:
                tmp_doc = doc.copy_with(text_md=sample)
                out = ports.translate.translate_md(
                    tmp_doc, src_lang=code, tgt_lang=tgt_lang, options=options, context=context
                )
                en_conf = en_det.english_confidence(out.text_md)
                s = sim.similarity(sample, out.text_md)
                score = float(en_conf) - 0.5 * float(s)  # deterministic composite
                if score > best_score:
                    best_score = score
                    best_code = code
            except Exception:
                continue
        return best_code or (codes[0] if codes else (src_lang or "auto"))

    selected_src = src_lang or "auto"
    # Decide if we should probe based on thresholds
    need_probe = False
    if hasattr(ports.langid, "detect_topk") and topk_info:
        top1 = topk_info[0]
        top2 = topk_info[1] if len(topk_info) > 1 else None
        margin = (
            abs((top1.get("score", 0.0) or 0.0) - (top2.get("score", 0.0) or 0.0)) if top2 else 1.0
        )
        low_conf = (detected_conf or 0.0) < tau_low
        close = margin < delta_close
        need_probe = low_conf or close

    if need_probe and hasattr(ports, "english_detector") and hasattr(ports, "similarity"):
        cand_codes = [
            c
            for c in _gather_candidates()
            if c in (cand_scope or [c for c in _gather_candidates()])
        ]
        if cand_codes:
            selected_src = _probe_choose(cand_codes)

    # Deterministic retry ladder with validation
    tried: list[tuple[str, str]] = []  # (engine_name, src_code)
    engines: list[tuple[str, TranslatePort]] = [("primary", ports.translate)]
    if hasattr(ports, "translate_secondary") and getattr(ports, "translate_secondary", None):
        engines.append(("secondary", getattr(ports, "translate_secondary")))

    # Build ordered source list: selected, then remaining topk in order
    sources = []
    if selected_src and selected_src != "auto":
        sources.append(selected_src)
    for c in _gather_candidates():
        if c not in sources and c != "en":
            sources.append(c)
    if not sources and src_lang:
        sources = [src_lang]

    validation_meta: dict[str, Any] = {
        "tau_en": tau_en,
        "similarity_noop_threshold": sim_noop,
        "selected_src": selected_src,
        "topk": topk_info,
        "probe": need_probe,
    }

    en_doc: MarkdownDoc | None = None
    last_error: dict[str, Any] | None = None
    attempts = 0
    for eng_name, engine in engines:
        for src in sources:
            if attempts >= max_retries:
                break
            attempts += 1
            tried.append((eng_name, src))
            try:
                t_tr = time.perf_counter()
                cand_doc = engine.translate_md(
                    doc, src_lang=src, tgt_lang=tgt_lang, options=options, context=context
                )
                translate_ms = (time.perf_counter() - t_tr) * 1000.0
            except TranslationError as e:
                last_error = e.to_dict()
                continue

            # Post-translation validation when advanced ports present
            if hasattr(ports, "english_detector") and hasattr(ports, "similarity"):
                en_det = getattr(ports, "english_detector", None)
                sim = getattr(ports, "similarity", None)
                if en_det and sim:
                    try:
                        en_conf = float(en_det.english_confidence(cand_doc.text_md))
                    except Exception:
                        en_conf = 0.0
                    try:
                        similarity = float(sim.similarity(doc.text_md, cand_doc.text_md))
                    except Exception:
                        similarity = 1.0
                    validation_meta.update(
                        {
                            "en_confidence": en_conf,
                            "similarity": similarity,
                            "engine_attempt": eng_name,
                            "src_attempt": src,
                        }
                    )
                    if en_conf >= tau_en and similarity < sim_noop:
                        en_doc = cand_doc
                        break  # success
                    else:
                        last_error = {
                            "code": "post_validation_failed",
                            "message": "output did not meet EN confidence or similarity thresholds",
                            "details": {"en_conf": en_conf, "similarity": similarity},
                        }
                        continue
            # If no advanced validators, accept first success
            en_doc = cand_doc
            break
        if en_doc is not None:
            break

    if en_doc is None:
        return {
            "status": "failed",
            "src_lang": src_lang,
            "tgt_lang": tgt_lang,
            "written_path": None,
            "meta": {
                "engine": getattr(ports.translate, "capabilities", lambda: {})(),
                "timings": {
                    "detect_ms": detect_ms,
                    "translate_ms": translate_ms,
                    "write_ms": write_ms,
                },
                "routing": validation_meta,
                "attempts": tried,
            },
            "error": last_error or {"code": "unresolved", "message": "exhausted retry ladder"},
        }

    # Validation of invariants on returned MarkdownDoc
    try:
        validate_markdown_doc(en_doc)
        if en_doc.variant != "english":
            if strict:
                return {
                    "status": "failed",
                    "src_lang": src_lang,
                    "tgt_lang": tgt_lang,
                    "written_path": None,
                    "meta": {
                        "timings": {
                            "detect_ms": detect_ms,
                            "translate_ms": translate_ms,
                            "write_ms": 0.0,
                        }
                    },
                    "error": {
                        "code": "invalid_variant",
                        "message": f"expected variant 'english', got '{en_doc.variant}'",
                    },
                }
            # non-strict: coerce for writing
            en_doc = en_doc.copy_with(variant="english")
        if (en_doc.lang or "").lower() != "en":
            if strict:
                return {
                    "status": "failed",
                    "src_lang": src_lang,
                    "tgt_lang": tgt_lang,
                    "written_path": None,
                    "meta": {
                        "timings": {
                            "detect_ms": detect_ms,
                            "translate_ms": translate_ms,
                            "write_ms": 0.0,
                        }
                    },
                    "error": {
                        "code": "invalid_lang",
                        "message": f"expected lang 'en', got '{en_doc.lang}'",
                    },
                }
            en_doc = en_doc.copy_with(lang="en")
    except ValueError as e:
        return {
            "status": "failed",
            "src_lang": src_lang,
            "tgt_lang": tgt_lang,
            "written_path": None,
            "meta": {
                "timings": {"detect_ms": detect_ms, "translate_ms": translate_ms, "write_ms": 0.0}
            },
            "error": {"code": "invalid_markdown_doc", "message": str(e)},
        }

    # Dry-run planning only
    if dry_run:
        try:
            planned = _compute_target_paths(ports.writer, writer_ctx, en_doc)
            return {
                "status": "created",
                "src_lang": src_lang,
                "tgt_lang": tgt_lang,
                "written_path": planned["out_md_path"],
                "meta": {
                    "engine": getattr(ports.translate, "capabilities", lambda: {})(),
                    "glossary_id": glossary_id,
                    "timings": {
                        "detect_ms": detect_ms,
                        "translate_ms": translate_ms,
                        "write_ms": 0.0,
                    },
                    "routing": validation_meta,
                    "dry_run": True,
                },
            }
        except WriteError as e:
            return {
                "status": "failed",
                "src_lang": src_lang,
                "tgt_lang": tgt_lang,
                "written_path": None,
                "meta": {
                    "timings": {
                        "detect_ms": detect_ms,
                        "translate_ms": translate_ms,
                        "write_ms": 0.0,
                    }
                },
                "error": e.to_dict(),
            }

    # Write for real
    try:
        t_w = time.perf_counter()
        wr = ports.writer.write(en_doc, writer_ctx)
        write_ms = (time.perf_counter() - t_w) * 1000.0
        return {
            "status": "created",
            "src_lang": src_lang,
            "tgt_lang": tgt_lang,
            "written_path": wr.get("out_md_path", out_md_path),
            "meta": {
                "engine": getattr(ports.translate, "capabilities", lambda: {})(),
                "writer": {
                    "status": wr.get("status"),
                    "sidecar_written": wr.get("sidecar_written", False),
                },
                "glossary_id": glossary_id,
                "timings": {
                    "detect_ms": detect_ms,
                    "translate_ms": translate_ms,
                    "write_ms": write_ms,
                },
                "routing": validation_meta,
            },
        }
    except WriteError as e:
        return {
            "status": "failed",
            "src_lang": src_lang,
            "tgt_lang": tgt_lang,
            "written_path": None,
            "meta": {
                "timings": {
                    "detect_ms": detect_ms,
                    "translate_ms": translate_ms,
                    "write_ms": write_ms,
                }
            },
            "error": e.to_dict(),
        }
    finally:
        _ = (
            time.perf_counter() - t0
        ) * 1000.0  # total time measured but not returned explicitly (kept local)


# -----------------------------
# Public API: batch
# -----------------------------


def ensure_english_for_batch(
    docs: Iterable[MarkdownDoc],
    ports: _PortsBundle,
    cfg: EnglishCfg | Mapping[str, Any] | object,
    context: dict[str, Any] | None = None,
) -> BatchEnglishResult:
    """Orchestrate creation of the English variant for many documents.

    Parameters:
        docs: Iterable of MarkdownDoc representing originals.
        ports: Ports bundle; see ensure_english_variant for attributes.
        cfg: Same config as single-doc with batch additions:
            - workers: Int parallel workers (default 1).
            - on_error: "skip" | "fail_fast" policy (default "skip").
        context: Optional dict propagated to underlying ports.

    Returns:
        BatchEnglishResult: Aggregates including counts, ordered per-doc results,
        and wall clock timings. The order of results matches the input order.

    Concurrency and determinism:
        - Uses bounded parallelism according to workers.
        - Results are assembled in input order; write operations are delegated to the writer port
          which must be atomic and safe under concurrency.
    """

    t_batch0 = time.perf_counter()
    workers = int(_cfg_get(cfg, "workers", 1) or 1)
    on_error = str(_cfg_get(cfg, "on_error", "skip") or "skip")

    # Normalize to list to preserve order and support multiple passes if needed
    docs_list = list(docs)
    total = len(docs_list)

    results: list[EnsureEnglishResult] = [
        {
            "status": "failed",
            "src_lang": None,
            "tgt_lang": (_cfg_get(cfg, "tgt_lang", "en") or "en").lower(),
            "written_path": None,
            "meta": {"error": "not_processed"},
            "error": {"code": "not_processed", "message": "not processed yet"},
        }
        for _ in range(total)
    ]

    def _process_one(idx_doc: tuple[int, MarkdownDoc]) -> tuple[int, EnsureEnglishResult]:
        i, d = idx_doc
        res = ensure_english_variant(d, ports, cfg, context=context)
        return i, res

    cancelled = False
    if workers <= 1:
        for i, d in enumerate(docs_list):
            res = ensure_english_variant(d, ports, cfg, context=context)
            results[i] = res
            if on_error == "fail_fast" and res.get("status") == "failed":
                # Short-circuit remaining with a marker
                for j in range(i + 1, total):
                    results[j] = {
                        "status": "failed",
                        "src_lang": None,
                        "tgt_lang": (_cfg_get(cfg, "tgt_lang", "en") or "en").lower(),
                        "written_path": None,
                        "meta": {"error": "skipped_due_to_fail_fast"},
                        "error": {
                            "code": "short_circuited",
                            "message": "fail_fast: skipped remaining items",
                        },
                    }
                cancelled = True
                break
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ensure_en") as ex:
            futures = [ex.submit(_process_one, (i, d)) for i, d in enumerate(docs_list)]
            for i in range(total):
                if cancelled:
                    break
                fut = futures[i]
                try:
                    idx, res = fut.result()
                except (
                    Exception
                ) as e:  # Guardrail: convert unexpected errors to domain-like failure
                    res = {
                        "status": "failed",
                        "src_lang": None,
                        "tgt_lang": (_cfg_get(cfg, "tgt_lang", "en") or "en").lower(),
                        "written_path": None,
                        "meta": {"exception": e.__class__.__name__},
                        "error": {"code": "unexpected", "message": str(e)},
                    }
                    idx = i
                results[idx] = res
                if on_error == "fail_fast" and res.get("status") == "failed":
                    # Mark remaining as short-circuited
                    for j in range(i + 1, total):
                        results[j] = {
                            "status": "failed",
                            "src_lang": None,
                            "tgt_lang": (_cfg_get(cfg, "tgt_lang", "en") or "en").lower(),
                            "written_path": None,
                            "meta": {"error": "skipped_due_to_fail_fast"},
                            "error": {
                                "code": "short_circuited",
                                "message": "fail_fast: skipped remaining items",
                            },
                        }
                    cancelled = True
                    break

    # Aggregate counts
    created = sum(1 for r in results if r.get("status") == "created")
    skipped_exists = sum(1 for r in results if r.get("status") == "skipped_exists")
    skipped_already_en = sum(1 for r in results if r.get("status") == "skipped_already_en")
    failed = sum(1 for r in results if r.get("status") == "failed")

    wall_ms = (time.perf_counter() - t_batch0) * 1000.0

    return {
        "total": total,
        "created": created,
        "skipped_exists": skipped_exists,
        "skipped_already_en": skipped_already_en,
        "failed": failed,
        "results": results,
        "wall_millis": wall_ms,
        "engines": getattr(ports.translate, "capabilities", lambda: {})(),
    }
