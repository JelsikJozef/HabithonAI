"""
ct2_nllb
=========

Deterministic, offline Markdown translator using CTranslate2 with an NLLB-200
(or distilled) model. Only human text nodes inside Markdown are translated;
Markdown structure is preserved byte-for-byte outside translated spans.

This adapter implements the project's TranslatePort contract and integrates
with the local markdown_segmenter for lossless extraction/recombination.

Design highlights
-----------------
- Lazy model/tokenizer load via load() to avoid heavy work in __init__.
- Deterministic decoding: fixed beam search params; no sampling.
- Optional glossary pre/post passes on plain text segments.
- Optional cache for per-segment translations with stable keys.
- Robust error mapping: vendor exceptions are converted to TranslationError
  with short, stable reason codes in details.

External runtime deps
---------------------
- ctranslate2
- sentencepiece

Neither library is imported at module import time to keep this file importable
when the environment lacks those dependencies. They are loaded on demand by
load().
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from preprocessing.domain.errors import TranslationError
from preprocessing.domain.models_markdown import MarkdownDoc
from preprocessing.domain.ports import CachePort, GlossaryPort, TranslatePort

# Local, lossless Markdown segmentation utilities
from . import markdown_segmenter as mdseg


@dataclass(frozen=True)
class _EngineParams:
    """Deterministic decoding parameters for CTranslate2 engine.

    Attributes
    ----------
    beam_size : int
        Fixed beam width; sampling is disabled for determinism.
    length_penalty : float
        Length normalization exponent applied during beam search.
    compute_type : str
        CTranslate2 compute type ("int8", "int8_float16", "int16", "float16", "float32").
    device : str
        Target device: "cpu", "cuda", or "auto".
    num_threads : Optional[int]
        Optional threads hint for the CT2 runtime; if None, use library default.
    max_batch_size : int
        Max number of segments per inference batch.
    max_tokens : int
        Upper bound on tokens per input segment; segments are split internally when exceeded.
    seed : Optional[int]
        Optional RNG seed for backends that reference it; not required for deterministic beam search.
    """

    beam_size: int
    length_penalty: float
    compute_type: str
    device: str
    num_threads: int | None
    max_batch_size: int
    max_tokens: int
    seed: int | None


class NllbCTranslate2(TranslatePort):
    """Offline, deterministic Markdown translation adapter using CTranslate2 NLLB.

    Responsibilities
    ---------------
    - Load a local CTranslate2-converted NLLB model and its SentencePiece tokenizer.
    - Map simple language codes ("sk","de","cs","pl","hu","en") to NLLB tags
      ("slk_Latn","deu_Latn","ces_Latn","pol_Latn","hun_Latn","eng_Latn").
    - Segment Markdown into translatable spans, translate in deterministic batches,
      and recombine into byte-stable Markdown (outside translated spans).
    - Integrate optional glossary (pre/post) and cache.
    - Never leak vendor exceptions; map to TranslationError with stable reason codes.

    Determinism
    -----------
    - Decoding uses fixed beam search and disables sampling.
    - Cache keys include an engine fingerprint that changes if model files change.
    - Identical inputs and configuration yield identical outputs and metadata.
    """

    def __init__(
        self,
        model_dir: str,
        *,
        src_lang_map: dict[str, str] | None = None,
        tgt_lang_code: str = "eng_Latn",
        compute_type: str = "int8",
        device: str = "cpu",
        num_threads: int | None = None,
        beam_size: int = 4,
        length_penalty: float = 1.0,
        max_batch_size: int = 32,
        max_tokens: int = 256,
        segmenter_options: dict[str, Any] | None = None,
        pre_space_policy: str = "preserve",
        post_space_policy: str = "preserve",
        glossary_mode: str = "none",
        cache_enabled: bool = True,
        seed: int | None = None,
        glossary: GlossaryPort | None = None,
        cache: CachePort | None = None,
        segmenter: Any | None = None,
    ) -> None:
        """Construct a new adapter with lazy-loaded engine.

        Parameters
        ----------
        model_dir : str
            Filesystem path to a CTranslate2-converted NLLB model directory containing
            model artifacts (e.g., model.bin, model.config.json) and a SentencePiece
            model file (e.g., sentencepiece.model or spm.model).
        src_lang_map : dict | None, keyword-only
            Mapping from ISO-like codes to NLLB tags. Defaults to common languages
            used in this project if None is provided.
        tgt_lang_code : str, keyword-only
            NLLB language tag for the desired target language (default "eng_Latn").
        compute_type : str, keyword-only
            CTranslate2 compute type ("int8"|"int8_float16"|"int16"|"float16"|"float32").
        device : str, keyword-only
            Execution device: "cpu" or "cuda" ("auto" is also accepted).
        num_threads : int | None, keyword-only
            Optional threads hint for CT2. When None, CT2 selects a default.
        beam_size : int, keyword-only
            Fixed beam width for deterministic decoding. Default 4.
        length_penalty : float, keyword-only
            Length normalization factor for beam search. Default 1.0.
        max_batch_size : int, keyword-only
            Max number of segments to translate per batch. Default 32.
        max_tokens : int, keyword-only
            Max tokens per input segment; segments are further split internally
            (without crossing Markdown node boundaries) if exceeded. Default 256.
        segmenter_options : dict | None, keyword-only
            Options forwarded to markdown_segmenter.extract_segments. May include
            segment_max_chars, translate_link_label, translate_alt_text, etc.
        pre_space_policy : str, keyword-only
            Whitespace preservation strategy before translation ("preserve" only; reserved for future use).
        post_space_policy : str, keyword-only
            Whitespace preservation strategy after translation ("preserve" only; reserved for future use).
        glossary_mode : str, keyword-only
            One of {"pre","post","both","none"}; effective only when ``glossary`` is provided.
        cache_enabled : bool, keyword-only
            If True and ``cache`` is provided, use the cache for segments.
        seed : int | None, keyword-only
            Optional RNG seed; not required for determinism under beam search but
            recorded in metadata for auditing.
        glossary : GlossaryPort | None, keyword-only
            Optional glossary port applied to plain text segments per ``glossary_mode``.
        cache : CachePort | None, keyword-only
            Optional cache port used for deterministic segment caching.
        segmenter : Any | None, keyword-only
            Optional handle to a segmenter module; if provided, it must expose
            extract_segments and recombine compatible with markdown_segmenter.
            When None, the local module is used.

        Notes
        -----
        - Validates that ``model_dir`` exists without loading model files.
        - Stores configuration and prepares a deterministic engine params object.
        - Heavy resources are loaded lazily via :meth:`load`.
        """
        if not isinstance(model_dir, str) or not model_dir:
            raise TranslationError(
                "invalid settings",
                {"reason": "SETTINGS", "message": "model_dir must be a non-empty string"},
            )
        if not os.path.isdir(model_dir):
            raise TranslationError(
                "model not available",
                {
                    "reason": "MODEL_LOAD_FAILED",
                    "model_dir": model_dir,
                    "message": "model directory not found",
                },
            )

        self._model_dir = os.path.abspath(model_dir)
        self._src_lang_map = dict(
            src_lang_map
            or {
                "sk": "slk_Latn",
                "de": "deu_Latn",
                "cs": "ces_Latn",
                "pl": "pol_Latn",
                "hu": "hun_Latn",
                "en": "eng_Latn",
            }
        )
        self._tgt_lang_code = tgt_lang_code
        self._segmenter_options = dict(segmenter_options or {})
        self._glossary_mode = glossary_mode
        self._cache_enabled = bool(cache_enabled)
        self._glossary = glossary
        self._cache = cache
        self._segmenter = segmenter  # optional custom, otherwise use local imports
        self._pre_space_policy = pre_space_policy
        self._post_space_policy = post_space_policy

        # Engine params kept together for deterministic behavior
        self._eng = _EngineParams(
            beam_size=int(beam_size),
            length_penalty=float(length_penalty),
            compute_type=str(compute_type),
            device=str(device),
            num_threads=int(num_threads) if num_threads is not None else None,
            max_batch_size=int(max_batch_size),
            max_tokens=int(max_tokens),
            seed=int(seed) if seed is not None else None,
        )

        # Lazy-loaded resources
        self._translator = None  # type: ignore[var-annotated]
        self._sp = None  # SentencePieceProcessor; set in load()
        self._fingerprint = self._compute_engine_fingerprint()  # computed without heavy loads

    # -----------------------------
    # Lifecycle
    # -----------------------------

    def load(self) -> None:
        """Load model/tokenizer and prepare the CTranslate2 translator.

        Idempotent and safe to call multiple times. On success, subsequent calls
        are no-ops. On failure, raises TranslationError with reason codes:
            - "MODEL_LOAD_FAILED" when the CT2 translator can't be created
            - "TOKENIZER_LOAD_FAILED" when the SentencePiece model can't be loaded

        Raises
        ------
        TranslationError
            When model or tokenizer loading fails.
        """
        if self._translator is not None and self._sp is not None:
            return

        # Attempt dynamic imports to avoid hard dependency at module import time
        try:
            import ctranslate2 as ct2  # type: ignore
        except Exception as exc:  # pragma: no cover - environment-specific
            raise TranslationError(
                "model not available",
                {
                    "reason": "MODEL_LOAD_FAILED",
                    "message": "ctranslate2 not installed or failed to import",
                    "details": str(exc.__class__.__name__),
                },
            )

        # Create translator
        try:
            self._translator = ct2.Translator(
                self._model_dir, device=self._eng.device, compute_type=self._eng.compute_type
            )  # type: ignore[assignment]
        except Exception as exc:  # pragma: no cover - depends on environment
            raise TranslationError(
                "model not available",
                {
                    "reason": "MODEL_LOAD_FAILED",
                    "message": "failed to initialize CTranslate2 translator",
                    "model_dir": self._model_dir,
                    "details": str(exc.__class__.__name__),
                },
            )

        # Load SentencePiece tokenizer
        sp_model_candidates = [
            os.path.join(self._model_dir, "sentencepiece.model"),
            os.path.join(self._model_dir, "spm.model"),
        ]
        sp_path = next((p for p in sp_model_candidates if os.path.isfile(p)), None)
        if sp_path is None:
            # Some conversions store tokenizer.json; we require sentencepiece for NLLB
            raise TranslationError(
                "tokenizer not available",
                {
                    "reason": "TOKENIZER_LOAD_FAILED",
                    "message": "sentencepiece model file not found",
                    "model_dir": self._model_dir,
                },
            )

        try:
            import sentencepiece as spm  # type: ignore

            sp = spm.SentencePieceProcessor()  # type: ignore[attr-defined]
            sp.Load(sp_path)  # type: ignore[attr-defined]
            self._sp = sp
        except Exception as exc:  # pragma: no cover - environment-specific
            raise TranslationError(
                "tokenizer not available",
                {
                    "reason": "TOKENIZER_LOAD_FAILED",
                    "message": "failed to load sentencepiece model",
                    "model_dir": self._model_dir,
                    "details": str(exc.__class__.__name__),
                },
            )

        # Compute engine fingerprint to embed in cache keys and metadata
        self._fingerprint = self._compute_engine_fingerprint()

    def close(self) -> None:
        """Release translator/tokenizer resources; idempotent.

        Safe to call multiple times. The method clears references to allow GC
        to reclaim resources. No error is raised on missing resources.
        """
        self._translator = None
        self._sp = None
        # fingerprint intentionally retained; it reflects model_dir state at load

    # -----------------------------
    # Public API (TranslatePort)
    # -----------------------------

    def translate_md(
        self,
        doc: MarkdownDoc,
        src_lang: str,
        tgt_lang: str,
        options: dict[str, Any] | None = None,
        *,
        context: dict[str, Any] | None = None,
    ) -> MarkdownDoc:
        """Translate only Markdown text nodes to English while preserving structure.

        Parameters
        ----------
        doc : MarkdownDoc
            Source Markdown document (variant "original"). The adapter never mutates
            this object; it returns a new MarkdownDoc instance.
        src_lang : str
            ISO-like source language code (e.g., "sk", "de"). If "auto", this
            adapter rejects the request and expects upstream langid resolution.
        tgt_lang : str, optional
            Target language code; currently only "en" is supported. Default "en".
        options : dict | None, optional
            Optional runtime overrides merged into constructor configuration. Recognized keys include:
            - style: "natural" | "literal" (advisory only; no functional change here)
            - glossary_id: Optional[str] used when glossary_mode is not "none"
            - max_segment_chars: Optional[int] forwarded to segmentation
            - any markdown_segmenter options (translate_link_label, translate_alt_text, ...)
        context : dict | None, keyword-only
            Optional telemetry context (unused by this adapter but safe to pass).

        Returns
        -------
        MarkdownDoc
            New document with variant="english", lang="en", translated text_md,
            and metadata extended with translator fingerprint, segment counts,
            cache/glossary hints, and timings.

        Raises
        ------
        TranslationError
            On unsupported languages, model/tokenizer load issues, segmentation
            failures, batch/runtime failures, or recomposition errors. The error's
            details.reason conveys a stable short code, e.g., "UNSUPPORTED_LANG",
            "MODEL_LOAD_FAILED", "BATCH_FAILED", "RECOMPOSE_FAILED".
        """
        # Validate target
        if (tgt_lang or "en").lower() != "en":
            raise TranslationError(
                "unsupported language pair",
                {"reason": "UNSUPPORTED_LANG", "src": src_lang, "tgt": tgt_lang},
            )
        # Resolve source lang to NLLB tag
        if src_lang is None or src_lang.lower() == "auto":
            raise TranslationError(
                "source language required",
                {"reason": "UNSUPPORTED_LANG", "message": "src_lang must be explicit (not 'auto')"},
            )
        src_lang_lc = src_lang.lower()
        if src_lang_lc not in self._src_lang_map:
            raise TranslationError(
                "unsupported language pair", {"reason": "UNSUPPORTED_LANG", "src": src_lang_lc}
            )
        src_tag = self._src_lang_map[src_lang_lc]
        tgt_tag = self._tgt_lang_code

        # Prepare options (before any heavy load)
        opts = dict(options or {})
        glossary_id: str | None = opts.get("glossary_id")
        seg_opts = dict(self._segmenter_options)
        if "max_segment_chars" in opts and opts["max_segment_chars"] is not None:
            seg_opts["segment_max_chars"] = int(opts["max_segment_chars"])  # alias
        for k in (
            "segment_max_chars",
            "translate_link_label",
            "translate_alt_text",
            "translate_table_cells",
            "preserve_whitespace",
            "collapse_softbreaks",
            "language_hint",
        ):
            if k in opts:
                seg_opts[k] = opts[k]

        # 1) Segment Markdown (no model load yet)
        t_seg0 = time.perf_counter()
        try:
            segments, plan = (self._segmenter or mdseg).extract_segments(
                doc.text_md, options=seg_opts
            )
        except mdseg.SegmentationError as exc:
            raise TranslationError(
                "segmentation failed", {"reason": "SEGMENT_FAILED", "message": str(exc)}
            )
        except Exception as exc:
            raise TranslationError(
                "segmentation failed",
                {"reason": "SEGMENT_FAILED", "message": exc.__class__.__name__},
            )
        t_seg1 = time.perf_counter()

        # Short-circuit if nothing to translate (still no model load)
        if not segments:
            # Reuse original text; still attach metadata
            meta = dict(doc.meta or {})
            meta.setdefault("translator", {})
            meta["translator"] = {
                "engine": "ct2-nllb",
                "model_dir": os.path.basename(self._model_dir),
                "compute_type": self._eng.compute_type,
                "device": self._eng.device,
                "beam_size": self._eng.beam_size,
                "length_penalty": self._eng.length_penalty,
                "fingerprint": self._fingerprint,
            }
            meta.setdefault("langs", {})
            meta["langs"] = {
                "src": src_lang_lc,
                "src_tag": src_tag,
                "tgt": "en",
                "tgt_tag": tgt_tag,
            }
            meta["segments"] = {
                "total": 0,
                "cached": 0,
                "batched": 0,
                "max_tokens": self._eng.max_tokens,
                "max_batch_size": self._eng.max_batch_size,
            }
            meta["timings_ms"] = {
                "segment": (t_seg1 - t_seg0) * 1000.0,
                "translate": 0.0,
                "recombine": 0.0,
            }
            if glossary_id is not None:
                meta["glossary_id"] = glossary_id
            meta["cache_enabled"] = bool(self._cache and self._cache_enabled)
            return doc.copy_with(variant="english", lang="en", text_md=doc.text_md, meta=meta)

        # 2) Pre-glossary if configured
        def apply_glossary_pre(text: str) -> str:
            if self._glossary and self._glossary_mode in {"pre", "both"}:
                try:
                    return self._glossary.apply(
                        text,
                        src_lang=src_lang_lc,
                        tgt_lang="en",
                        mode="pre",
                        glossary_id=glossary_id,
                    )
                except (
                    Exception
                ) as exc:  # Keep glossary failures contained under translation umbrella
                    raise TranslationError(
                        "glossary failed", {"reason": "GLOSSARY_FAILED", "message": str(exc)}
                    )
            return text

        # 3) Cache + batch plan (deterministic)
        engine_key = self._fingerprint or ""
        total = len(segments)
        cached = 0
        batched = 0
        translated_map: dict[str, str] = {}
        to_translate: list[tuple[str, str]] = []  # (seg_id, text)

        for seg in segments:
            seg_text = apply_glossary_pre(seg.text)
            cache_val: str | None = None
            if self._cache and self._cache_enabled:
                key = self._make_cache_key(seg_text, src_tag, tgt_tag, engine_key, glossary_id)
                try:
                    cache_val = self._cache.get(key)
                except Exception:
                    cache_val = (
                        None  # Cache failures are non-fatal here; continue deterministically
                    )
            if cache_val is not None:
                translated_map[seg.id] = cache_val
                cached += 1
            else:
                to_translate.append((seg.id, seg_text))

        # Load model/tokenizer only if there are misses to translate
        if to_translate and (
            self._translator is None or self._sp is None or self._fingerprint is None
        ):
            self.load()

        # 4) Batch translate misses deterministically
        t_tr0 = time.perf_counter()
        if to_translate:
            # Enforce token limits by splitting inside adapter, then re-joining per original id
            # We translate in batches over subparts and reassemble by original segment id.
            batch_texts: list[str] = []
            batch_ids: list[str] = []  # original seg id for each subpart

            for seg_id, text in to_translate:
                # Encode to tokens to check length; split conservatively by sentences/words
                subparts = self._split_by_tokens(text, self._eng.max_tokens)
                for sub in subparts:
                    batch_texts.append(sub)
                    batch_ids.append(seg_id)

            # Execute in fixed-size batches
            out_texts: list[str] = []
            out_ids: list[str] = []
            for i in range(0, len(batch_texts), self._eng.max_batch_size):
                sl = slice(i, min(i + self._eng.max_batch_size, len(batch_texts)))
                chunk_inputs = batch_texts[sl]
                chunk_ids = batch_ids[sl]
                try:
                    chunk_outputs = self._translate_batch_texts(
                        chunk_inputs, src_tag=src_tag, tgt_tag=tgt_tag
                    )
                except TranslationError:
                    raise
                except Exception as exc:
                    raise TranslationError(
                        "batch failed",
                        {
                            "reason": "BATCH_FAILED",
                            "message": str(exc.__class__.__name__),
                            "count": len(chunk_inputs),
                        },
                    )
                out_texts.extend(chunk_outputs)
                out_ids.extend(chunk_ids)
                batched += 1

            # Reassemble subparts into full segment translations
            assert len(out_texts) == len(out_ids)
            per_seg: dict[str, list[str]] = {}
            for sid, txt in zip(out_ids, out_texts):
                per_seg.setdefault(sid, []).append(txt)
            for sid, parts in per_seg.items():
                full = "".join(parts)
                # 5) Post-glossary if configured
                if self._glossary and self._glossary_mode in {"post", "both"}:
                    try:
                        full = self._glossary.apply(
                            full,
                            src_lang=src_lang_lc,
                            tgt_lang="en",
                            mode="post",
                            glossary_id=glossary_id,
                        )
                    except Exception as exc:
                        raise TranslationError(
                            "glossary failed", {"reason": "GLOSSARY_FAILED", "message": str(exc)}
                        )
                translated_map[sid] = full

                # Populate cache
                if self._cache and self._cache_enabled:
                    try:
                        key = self._make_cache_key(
                            apply_glossary_pre(self._find_original_text(segments, sid)),
                            src_tag,
                            tgt_tag,
                            engine_key,
                            glossary_id,
                        )
                        self._cache.put(key, full)
                    except Exception:
                        pass  # cache failures are non-fatal
        t_tr1 = time.perf_counter()

        # 6) Recombine into Markdown
        translated_items = [
            {"id": sid, "text": translated_map[sid]} for sid in (e.id for e in segments)
        ]
        t_rc0 = time.perf_counter()
        try:
            md_text_en = (self._segmenter or mdseg).recombine(
                doc.text_md, translated_items, plan, options={}
            )
        except mdseg.RecombinationError as exc:
            raise TranslationError(
                "recompose failed", {"reason": "RECOMPOSE_FAILED", "message": str(exc)}
            )
        except Exception as exc:
            raise TranslationError(
                "recompose failed",
                {"reason": "RECOMPOSE_FAILED", "message": str(exc.__class__.__name__)},
            )
        t_rc1 = time.perf_counter()

        # 7) Assemble output MarkdownDoc with metadata
        meta = dict(doc.meta or {})
        meta.setdefault("translator", {})
        meta["translator"] = {
            "engine": "ct2-nllb",
            "model_dir": os.path.basename(self._model_dir),
            "compute_type": self._eng.compute_type,
            "device": self._eng.device,
            "beam_size": self._eng.beam_size,
            "length_penalty": self._eng.length_penalty,
            "fingerprint": self._fingerprint,
        }
        meta.setdefault("langs", {})
        meta["langs"] = {"src": src_lang_lc, "src_tag": src_tag, "tgt": "en", "tgt_tag": tgt_tag}
        meta["segments"] = {
            "total": total,
            "cached": cached,
            "batched": batched,
            "max_tokens": self._eng.max_tokens,
            "max_batch_size": self._eng.max_batch_size,
        }
        meta["timings_ms"] = {
            "segment": (t_seg1 - t_seg0) * 1000.0,
            "translate": (t_tr1 - t_tr0) * 1000.0,
            "recombine": (t_rc1 - t_rc0) * 1000.0,
        }
        if glossary_id is not None:
            meta["glossary_id"] = glossary_id
        meta["cache_enabled"] = bool(self._cache and self._cache_enabled)
        if self._eng.seed is not None:
            meta.setdefault("determinism", {})
            meta["determinism"]["seed"] = self._eng.seed

        return doc.copy_with(variant="english", lang="en", text_md=md_text_en, meta=meta)

    def capabilities(self) -> dict:
        """Return self-description and constraints for logging/validation.

        Returns
        -------
        dict
            Mapping describing engine identity and supported languages, for example:
            {"name": "ct2-nllb", "version": "<fingerprint>", "supports": {"src": [..], "tgt": ["en"]},
             "deterministic": True, "max_batch_size": 32, "compute_type": "int8", "device": "cpu"}
        """
        return {
            "name": "ct2-nllb",
            "version": self._fingerprint or os.path.basename(self._model_dir),
            "supports": {"src": sorted(list(self._src_lang_map.keys())), "tgt": ["en"]},
            "deterministic": True,
            "max_batch_size": self._eng.max_batch_size,
            "compute_type": self._eng.compute_type,
            "device": self._eng.device,
        }

    # -----------------------------
    # Internal helpers
    # -----------------------------

    def _translate_batch_texts(
        self, batch_texts: list[str], src_tag: str, tgt_tag: str
    ) -> list[str]:
        """Translate a batch of plain text segments deterministically.

        Behavioral contract
        --------------------
        - Encodes input texts using the SentencePiece tokenizer and prefixes the
          source language token. Uses the target language token as a decoding
          prefix to steer the model.
        - Runs CTranslate2 translate_batch with beam search (fixed params) and
          sampling disabled to ensure determinism.
        - Returns one output string per input string, aligned by index.
        - On any per-item failure, raises TranslationError("BATCH_FAILED").

        Parameters
        ----------
        batch_texts : list[str]
            Plain text segments to translate. Must be non-empty strings and
            already within token limits according to ``max_tokens``.
        src_tag : str
            NLLB language tag for the source language (e.g., "slk_Latn").
        tgt_tag : str
            NLLB language tag for the target language (e.g., "eng_Latn").

        Returns
        -------
        list[str]
            Translated text outputs corresponding to ``batch_texts``.

        Raises
        ------
        TranslationError
            When CTranslate2 translation fails for the batch.
        """
        if not batch_texts:
            return []
        assert self._translator is not None and self._sp is not None

        # Prepare tokenized inputs with source tag prefix
        inputs_tok: list[list[str]] = []
        for txt in batch_texts:
            # Encode to sentencepiece pieces; keep as strings for CT2
            pieces = list(self._sp.EncodeAsPieces(txt))  # type: ignore[attr-defined]
            inputs_tok.append([src_tag] + pieces)

        # Target prefix uses target language tag token
        target_prefix = [[tgt_tag] for _ in batch_texts]

        try:
            # We call translate_batch on tokenized inputs. Disable sampling; set beam size and length_penalty.
            # We request a single best hypothesis per input.
            results = self._translator.translate_batch(  # type: ignore[union-attr]
                inputs_tok,
                beam_size=self._eng.beam_size,
                length_penalty=self._eng.length_penalty,
                sampling_topk=0,  # ensure no sampling path
                sampling_topp=0,
                num_hypotheses=1,
                return_scores=False,
                target_prefix=target_prefix,
                max_batch_size=self._eng.max_batch_size,
                num_threads=self._eng.num_threads,
            )
        except Exception as exc:
            raise TranslationError(
                "batch failed",
                {
                    "reason": "BATCH_FAILED",
                    "message": str(exc.__class__.__name__),
                    "count": len(batch_texts),
                },
            )

        # Extract the single hypothesis and detokenize via sentencepiece
        outputs: list[str] = []
        try:
            for res in results:
                # Each result has hypotheses as list of token lists
                tokens = res.hypotheses[0] if hasattr(res, "hypotheses") else res[0]  # type: ignore[index]
                # Drop the leading target tag if present
                if tokens and tokens[0] == tgt_tag:
                    tokens = tokens[1:]
                text = self._sp.DecodePieces(tokens)  # type: ignore[attr-defined]
                outputs.append(text)
        except Exception as exc:
            raise TranslationError(
                "batch failed", {"reason": "BATCH_FAILED", "message": str(exc.__class__.__name__)}
            )

        if len(outputs) != len(batch_texts):
            raise TranslationError(
                "batch failed",
                {
                    "reason": "BATCH_FAILED",
                    "message": "mismatched outputs",
                    "got": len(outputs),
                    "exp": len(batch_texts),
                },
            )

        return outputs

    def _split_by_tokens(self, text: str, max_tokens: int) -> list[str]:
        """Split text into subparts so each subpart encodes to <= max_tokens tokens.

        Strategy
        --------
        - Uses SentencePiece to count tokens. If within limit, returns [text].
        - Otherwise splits deterministically at sentence boundaries (.!?:) or
        - whitespace without breaking characters, re-checking token counts per
          subpart until all are within the limit.

        Parameters
        ----------
        text : str
            Input plain text segment to split.
        max_tokens : int
            Token ceiling per subpart (includes room for a single language tag).

        Returns
        -------
        list[str]
            One or more contiguous subparts whose tokenized lengths are each
            within ``max_tokens`` when prefixed with the source tag.
        """
        assert self._sp is not None
        pieces = list(self._sp.EncodeAsPieces(text))  # type: ignore[attr-defined]
        if len(pieces) + 1 <= max_tokens:  # +1 for src tag
            return [text]

        # Fallback deterministic splitter on characters with sentence/space hints
        import re

        spans: list[tuple[int, int]] = []
        start = 0
        L = len(text)
        while start < L:
            end = min(L, start + max(max_tokens, 1) * 4)  # rough char window; will refine below
            # Look back for sentence boundary
            m = list(re.finditer(r"[.!?:]\s", text[start:end]))
            if m:
                end = start + m[-1].end()
            else:
                # Look back for whitespace
                m2 = list(re.finditer(r"\s+", text[start:end]))
                if m2:
                    end = start + m2[-1].start()
            if end <= start:
                end = min(L, start + max_tokens)  # worst-case progress
            spans.append((start, end))
            start = end

        parts: list[str] = []
        for a, b in spans:
            chunk = text[a:b]
            # Ensure each chunk meets token budget; shrink if necessary
            p = list(self._sp.EncodeAsPieces(chunk))  # type: ignore[attr-defined]
            if len(p) + 1 <= max_tokens:
                parts.append(chunk)
                continue
            # Shrink by splitting on spaces
            sub_start = 0
            while sub_start < len(chunk):
                sub_end = min(len(chunk), sub_start + len(chunk) // 2 or 1)
                # advance to next space
                space_idx = chunk.rfind(" ", sub_start + 1, sub_end)
                if space_idx != -1:
                    sub_end = space_idx
                sub = chunk[sub_start:sub_end]
                if not sub:
                    sub = chunk[sub_start : min(len(chunk), sub_start + 1)]
                    sub_end = sub_start + len(sub)
                parts.append(sub)
                sub_start = sub_end
        # Final pass: merge adjacent tiny parts when possible under budget
        merged: list[str] = []
        for txt in parts:
            if not merged:
                merged.append(txt)
                continue
            cand = merged[-1] + txt
            if len(list(self._sp.EncodeAsPieces(cand))) + 1 <= max_tokens:  # type: ignore[attr-defined]
                merged[-1] = cand
            else:
                merged.append(txt)
        return merged

    def _make_cache_key(
        self, text: str, src_tag: str, tgt_tag: str, engine_key: str, glossary_id: str | None
    ) -> str:
        """Return a deterministic cache key for one input segment.

        Format: sha256 of a stable tuple (text, src_tag, tgt_tag, engine_key, glossary_id or "").
        """
        h = hashlib.sha256()
        # Use ASCII unit separator to avoid collisions
        payload = "\x1f".join([text, src_tag, tgt_tag, engine_key, glossary_id or ""]).encode(
            "utf-8"
        )
        h.update(payload)
        return h.hexdigest()

    def _compute_engine_fingerprint(self) -> str:
        """Compute a lightweight fingerprint for engine drift detection.

        Combines the model directory basename, compute_type, device, and a short
        hash of file names and sizes/mtimes inside the model directory.
        """
        entries: list[tuple[str, int, int]] = []
        try:
            for name in sorted(os.listdir(self._model_dir)):
                p = os.path.join(self._model_dir, name)
                if not os.path.isfile(p):
                    continue
                st = os.stat(p)
                entries.append((name, int(st.st_size), int(st.st_mtime)))
        except Exception:
            entries = []
        h = hashlib.sha1()
        for name, size, mtime in entries:
            h.update(name.encode("utf-8"))
            h.update(size.to_bytes(8, "little", signed=False))
            h.update(mtime.to_bytes(8, "little", signed=False))
        short = h.hexdigest()[:10]
        return f"{os.path.basename(self._model_dir)}:{self._eng.compute_type}:{self._eng.device}:{short}"

    def _find_original_text(self, segments: Sequence[Any], seg_id: str) -> str:
        for s in segments:
            if getattr(s, "id", None) == seg_id:
                return getattr(s, "text", "")
        return ""
