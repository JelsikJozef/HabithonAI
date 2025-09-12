# filepath: /home/habithon1/Documents/HabithonAI/src/preprocessing/adapters/translate/marian_opus.py
"""
Marian OPUS (Transformers) Markdown translator (offline, deterministic).

Purpose
-------
Provide an offline translation adapter that implements the project's
TranslatePort using Helsinki-NLP / OPUS-MT Marian models via Transformers.
Only human text nodes inside Markdown are translated; Markdown structure is
preserved byte-for-byte outside translated spans. CPU-only is supported by
default and recommended for offline environments.

Design highlights
-----------------
- Lazy, per-language model/tokenizer loading; no heavy work in __init__.
- Deterministic decoding: fixed beam search; do_sample=False; optional
  no_repeat_ngram_size.
- Optional glossary pre/post passes on plain text segments.
- Optional segment cache with stable, engine-fingerprinted keys.
- Robust error mapping: any vendor exceptions are converted to TranslationError
  with stable reason codes.

External runtime deps
---------------------
- transformers (MarianMTModel, MarianTokenizer) and torch (CPU ok).
- Model artifacts present locally (local path or pre-populated HF cache).
  No network access at runtime: local_files_only=True is enforced by default.

Structure preservation
----------------------
This adapter integrates with the local markdown_segmenter to extract
translatable segments and recombine translations back into the original
Markdown, preserving code blocks, inline code, link/image destinations, and
layout delimiters.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from typing import Any

from preprocessing.domain.errors import TranslationError
from preprocessing.domain.models_markdown import MarkdownDoc
from preprocessing.domain.ports import CachePort, GlossaryPort, TranslatePort

# Local Markdown segmentation utilities
from . import markdown_segmenter as mdseg


@dataclass(frozen=True)
class _DecodingConfig:
    """Deterministic decoding and batching configuration for Marian.

    Attributes
    ----------
    num_beams : int
        Fixed beam width for deterministic decoding.
    length_penalty : float
        Length normalization factor during beam search.
    max_batch_size : int
        Max number of segments per generation call.
    max_new_tokens : int
        Upper bound on newly generated tokens per segment.
    no_repeat_ngram_size : Optional[int]
        If set, forbid repeating n-grams of this size during generation.
    device : str
        "cpu" or "cuda"; CPU recommended for offline policy.
    dtype : str
        Effective torch dtype; keep deterministic (e.g., "float32" on CPU).
    seed : Optional[int]
        Optional global seed for extra determinism in some backends.
    local_files_only : bool
        Enforce offline model loading via Transformers.
    hf_cache_dir : Optional[str]
        Explicit local HF cache directory path.
    """

    num_beams: int
    length_penalty: float
    max_batch_size: int
    max_new_tokens: int
    no_repeat_ngram_size: int | None
    device: str
    dtype: str
    seed: int | None
    local_files_only: bool
    hf_cache_dir: str | None


class MarianOpus(TranslatePort):
    """Offline English translator using local Marian OPUS models (Transformers).

    Responsibilities
    ---------------
    - Route ``src_lang`` to a configured local Marian model id/path (src→en).
    - Segment Markdown text nodes, batch-translate deterministically, and
      recombine into byte-stable Markdown.
    - Optional glossary pre/post normalization and cache integration.
    - Strict offline behavior: no network calls at runtime.

    Determinism
    -----------
    - Fixed beam search parameters, no sampling paths enabled.
    - Identical inputs and configuration yield identical outputs and metadata.

    Error mapping
    -------------
    - Vendor exceptions are converted to TranslationError with stable codes,
      e.g., "UNSUPPORTED_LANG", "MODEL_NOT_FOUND", "TOKENIZER_LOAD_FAILED",
      "BATCH_GENERATION_FAILED", "RECOMPOSE_FAILED".
    """

    def __init__(
        self,
        models: dict[str, str],
        *,
        device: str = "cpu",
        dtype: str = "auto",
        num_beams: int = 4,
        length_penalty: float = 1.0,
        max_batch_size: int = 16,
        max_new_tokens: int = 256,
        no_repeat_ngram_size: int | None = None,
        segmenter_options: dict[str, Any] | None = None,
        glossary_mode: str = "none",
        cache_enabled: bool = True,
        seed: int | None = None,
        local_files_only: bool = True,
        hf_cache_dir: str | None = None,
        glossary: GlossaryPort | None = None,
        cache: CachePort | None = None,
        segmenter: Any | None = None,
    ) -> None:
        """Construct a Marian OPUS adapter with lazy per-language loading.

        Parameters
        ----------
        models : dict
            Mapping of ISO-like source codes to local Marian model id/path for
            source→English pairs. Example: {"sk": "Helsinki-NLP/opus-mt-sk-en"}.
        device : str, keyword-only, default "cpu"
            Execution device ("cpu" or "cuda"). CPU is the offline default.
        dtype : str, keyword-only, default "auto"
            Effective torch dtype string (e.g., "float32"). "auto" lets the
            loader decide; on CPU it typically resolves to float32 deterministically.
        num_beams : int, keyword-only, default 4
            Fixed beam width for deterministic decoding.
        length_penalty : float, keyword-only, default 1.0
            Length normalization factor for beam search.
        max_batch_size : int, keyword-only, default 16
            Max number of segments per generation call.
        max_new_tokens : int, keyword-only, default 256
            Upper bound on newly generated tokens per segment.
        no_repeat_ngram_size : int | None, keyword-only
            If set, forbid repeating n-grams of this size during generation.
        segmenter_options : dict | None, keyword-only
            Options forwarded to markdown_segmenter.extract_segments.
        glossary_mode : str, keyword-only, default "none"
            One of {"pre","post","both","none"}; effective when ``glossary`` is provided.
        cache_enabled : bool, keyword-only, default True
            Use the cache port if injected.
        seed : int | None, keyword-only
            Optional global seed; generation already disables sampling.
        local_files_only : bool, keyword-only, default True
            Enforce offline model loading (no network).
        hf_cache_dir : str | None, keyword-only
            Optional explicit local HF cache directory.
        glossary : GlossaryPort | None, keyword-only
            Optional glossary port applied per ``glossary_mode``.
        cache : CachePort | None, keyword-only
            Optional cache port for per-segment translations.
        segmenter : Any | None, keyword-only
            Optional custom segmenter providing extract_segments/recombine.

        Notes
        -----
        - Does not load model weights in __init__; loading is lazy per language.
        - Stores decoding config and shallow validates inputs.
        """
        if not isinstance(models, dict) or not models:
            raise TranslationError(
                "invalid settings",
                {"reason": "SETTINGS", "message": "models mapping is required and non-empty"},
            )

        self._models: dict[str, str] = {k.lower(): v for k, v in models.items()}
        self._segmenter_options = dict(segmenter_options or {})
        self._glossary_mode = str(glossary_mode)
        self._cache_enabled = bool(cache_enabled)
        self._glossary = glossary
        self._cache = cache
        self._segmenter = segmenter

        self._eng = _DecodingConfig(
            num_beams=int(num_beams),
            length_penalty=float(length_penalty),
            max_batch_size=int(max_batch_size),
            max_new_tokens=int(max_new_tokens),
            no_repeat_ngram_size=(
                int(no_repeat_ngram_size) if no_repeat_ngram_size is not None else None
            ),
            device=str(device),
            dtype=str(dtype),
            seed=int(seed) if seed is not None else None,
            local_files_only=bool(local_files_only),
            hf_cache_dir=hf_cache_dir,
        )

        # Lazy-loaded engines per src language: src -> {"tokenizer", "model", "fingerprint", "model_id"}
        self._engines: dict[str, dict[str, Any]] = {}

    # -----------------------------
    # Lifecycle
    # -----------------------------

    def load(self, src_lang: str) -> None:
        """Load tokenizer/model for ``src_lang→en`` if not already loaded.

        Behavior
        ---------
        - Resolves the configured model id/path for ``src_lang``.
        - Loads MarianTokenizer and MarianMTModel from local files/cache only.
        - Initializes model on the configured device and dtype; sets eval() mode.
        - Caches the pair in-process for reuse; idempotent per language.

        Parameters
        ----------
        src_lang : str
            Lowercase ISO-like source language code (e.g., "sk", "de").

        Raises
        ------
        TranslationError
            With reasons "UNSUPPORTED_LANG", "MODEL_NOT_FOUND", or
            "TOKENIZER_LOAD_FAILED" when loading fails offline.
        """
        src = (src_lang or "").lower()
        if src not in self._models:
            raise TranslationError(
                "unsupported language pair",
                {"reason": "UNSUPPORTED_LANG", "src": src, "tgt": "en"},
            )
        if src in self._engines:
            return

        model_id = self._models[src]

        # Import heavy deps lazily
        try:
            from transformers import MarianMTModel, MarianTokenizer  # type: ignore
        except Exception as exc:  # pragma: no cover - environment specific
            raise TranslationError(
                "model not available",
                {
                    "reason": "MODEL_NOT_FOUND",
                    "message": "transformers not installed",
                    "details": exc.__class__.__name__,
                },
            )
        try:
            import torch  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise TranslationError(
                "model not available",
                {
                    "reason": "MODEL_NOT_FOUND",
                    "message": "torch not installed",
                    "details": exc.__class__.__name__,
                },
            )

        load_kwargs: dict[str, Any] = {
            "local_files_only": bool(self._eng.local_files_only),
        }
        if self._eng.hf_cache_dir:
            load_kwargs["cache_dir"] = self._eng.hf_cache_dir

        # Tokenizer
        try:
            tokenizer = MarianTokenizer.from_pretrained(model_id, **load_kwargs)
        except OSError as exc:
            raise TranslationError(
                "tokenizer not available",
                {
                    "reason": "TOKENIZER_LOAD_FAILED",
                    "message": "failed to load tokenizer",
                    "model": model_id,
                    "details": str(exc),
                },
            )
        except Exception as exc:  # pragma: no cover
            raise TranslationError(
                "tokenizer not available",
                {
                    "reason": "TOKENIZER_LOAD_FAILED",
                    "message": exc.__class__.__name__,
                    "model": model_id,
                },
            )

        # Model
        try:
            model = MarianMTModel.from_pretrained(model_id, **load_kwargs)
        except OSError as exc:
            raise TranslationError(
                "model not available",
                {
                    "reason": "MODEL_NOT_FOUND",
                    "message": "failed to load model",
                    "model": model_id,
                    "details": str(exc),
                },
            )
        except Exception as exc:  # pragma: no cover
            raise TranslationError(
                "model not available",
                {"reason": "MODEL_NOT_FOUND", "message": exc.__class__.__name__, "model": model_id},
            )

        # Device/dtype & eval
        try:
            device = self._eng.device
            if device not in {"cpu", "cuda"}:
                device = "cpu"
            model = model.to(device)
            model.eval()
            if self._eng.seed is not None:
                try:
                    torch.manual_seed(int(self._eng.seed))
                except Exception:
                    pass
        except Exception as exc:  # pragma: no cover
            raise TranslationError(
                "model not available",
                {
                    "reason": "MODEL_NOT_FOUND",
                    "message": "failed to initialize model on device",
                    "device": self._eng.device,
                    "details": exc.__class__.__name__,
                },
            )

        # Fingerprint per-language engine
        fingerprint = self._compute_model_fingerprint(model_id)
        self._engines[src] = {
            "tokenizer": tokenizer,
            "model": model,
            "fingerprint": fingerprint,
            "model_id": model_id,
        }

    def close(self) -> None:
        """Release loaded tokenizer/model resources; idempotent.

        Notes
        -----
        Clears strong references to allow garbage collection. Safe to call
        multiple times. Fingerprints are not cleared.
        """
        self._engines.clear()

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
        """Translate Markdown text nodes to English while preserving structure.

        Inputs
        ------
        doc : MarkdownDoc
            Source Markdown document (variant="original"). The adapter never
            mutates this object; it returns a new MarkdownDoc instance.
        src_lang : str
            ISO-like source language code (e.g., "sk", "de"). Must be explicit.
        tgt_lang : str
            Target language code. For this adapter, only "en" is supported.
        options : dict | None
            Runtime overrides merged into constructor options. Recognized keys:
            - style: "natural" | "literal" (advisory; no functional change)
            - glossary_id: Optional[str] identifying glossary rules to apply
            - max_segment_chars: Optional[int] (alias for segmenter option)
            - Any markdown_segmenter extraction option.
        context : dict | None (unused)
            Optional telemetry context. Ignored by this adapter.

        Returns
        -------
        MarkdownDoc
            New document with variant="english", lang="en", translated text_md,
            and metadata describing engine settings, counts, and timings.

        Raises
        ------
        TranslationError
            On unsupported languages/pairs, model/tokenizer load errors,
            segmentation/recombination failures, or batch generation errors.
        """
        # Target validation
        if (tgt_lang or "en").lower() != "en":
            raise TranslationError(
                "unsupported language pair",
                {"reason": "UNSUPPORTED_LANG", "src": (src_lang or "").lower(), "tgt": tgt_lang},
            )
        if src_lang is None or src_lang.lower() == "auto":
            raise TranslationError(
                "source language required",
                {"reason": "UNSUPPORTED_LANG", "message": "src_lang must be explicit (not 'auto')"},
            )

        src = src_lang.lower()
        if src not in self._models:
            raise TranslationError(
                "unsupported language pair", {"reason": "UNSUPPORTED_LANG", "src": src, "tgt": "en"}
            )

        # Merge options and segmenter options
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

        # Nothing to translate
        if not segments:
            meta = dict(doc.meta or {})
            meta.setdefault("translator", {})
            # Provide minimal fingerprint info (model id for src)
            engine_fp = self._compute_model_fingerprint(self._models[src])
            meta["translator"] = {
                "engine": "marian-opus",
                "model_id": self._models[src],
                "device": self._eng.device,
                "dtype": self._eng.dtype,
                "num_beams": self._eng.num_beams,
                "length_penalty": self._eng.length_penalty,
                "no_repeat_ngram_size": self._eng.no_repeat_ngram_size,
                "fingerprint": engine_fp,
            }
            meta.setdefault("langs", {})
            meta["langs"] = {"src": src, "tgt": "en"}
            meta["segments"] = {
                "total": 0,
                "cached": 0,
                "batched": 0,
                "max_new_tokens": self._eng.max_new_tokens,
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
            meta["local_files_only"] = bool(self._eng.local_files_only)
            return doc.copy_with(variant="english", lang="en", text_md=doc.text_md, meta=meta)

        # Glossary pre
        def _glossary_pre(text: str) -> str:
            if self._glossary and self._glossary_mode in {"pre", "both"}:
                try:
                    return self._glossary.apply(
                        text, src_lang=src, tgt_lang="en", mode="pre", glossary_id=glossary_id
                    )
                except Exception as exc:
                    raise TranslationError(
                        "glossary failed", {"reason": "GLOSSARY_FAILED", "message": str(exc)}
                    )
            return text

        # 2) Cache + plan misses (before loading heavy model)
        engine_key = self._compute_model_fingerprint(self._models[src])
        total = len(segments)
        cached = 0
        batched_calls = 0
        translated_map: dict[str, str] = {}
        to_translate: list[tuple[str, str]] = []  # (seg_id, preprocessed_text)

        for seg in segments:
            inp = _glossary_pre(seg.text)
            cache_val: str | None = None
            if self._cache and self._cache_enabled:
                key = self._make_cache_key(inp, src, "en", engine_key, glossary_id)
                try:
                    cache_val = self._cache.get(key)
                except Exception:
                    cache_val = None
            if cache_val is not None:
                translated_map[seg.id] = cache_val
                cached += 1
            else:
                to_translate.append((seg.id, inp))

        # Load model/tokenizer only if we have misses
        if to_translate:
            self.load(src)

        # 3) Translate misses deterministically in batches
        t_tr0 = time.perf_counter()
        if to_translate:
            # Potential safe sub-splitting by tokenizer token budget (input side)
            engine = self._engines[src]
            tokenizer = engine["tokenizer"]
            # We avoid truncation; split conservatively by sentences/words.
            batch_texts: list[str] = []
            batch_owner: list[str] = []  # original seg id

            for seg_id, text in to_translate:
                parts = self._split_by_tokens(
                    tokenizer, text, max_tokens_input=self._estimate_input_budget(tokenizer)
                )
                for p in parts:
                    batch_texts.append(p)
                    batch_owner.append(seg_id)

            # Execute in fixed-size batches
            out_texts: list[str] = []
            out_owner: list[str] = []
            for i in range(0, len(batch_texts), self._eng.max_batch_size):
                sl = slice(i, min(i + self._eng.max_batch_size, len(batch_texts)))
                chunk_inputs = batch_texts[sl]
                chunk_owner = batch_owner[sl]
                try:
                    chunk_outputs = self._translate_batch_texts(chunk_inputs, src_lang=src)
                except TranslationError:
                    raise
                except Exception as exc:
                    raise TranslationError(
                        "batch failed",
                        {
                            "reason": "BATCH_GENERATION_FAILED",
                            "message": str(exc.__class__.__name__),
                            "count": len(chunk_inputs),
                        },
                    )
                out_texts.extend(chunk_outputs)
                out_owner.extend(chunk_owner)
                batched_calls += 1

            # Reassemble subparts into full segment translations, apply post-glossary, and cache
            per_seg: dict[str, list[str]] = {}
            for sid, txt in zip(out_owner, out_texts):
                per_seg.setdefault(sid, []).append(txt)

            for sid, parts in per_seg.items():
                full = "".join(parts)
                # Post-glossary
                if self._glossary and self._glossary_mode in {"post", "both"}:
                    try:
                        full = self._glossary.apply(
                            full, src_lang=src, tgt_lang="en", mode="post", glossary_id=glossary_id
                        )
                    except Exception as exc:
                        raise TranslationError(
                            "glossary failed", {"reason": "GLOSSARY_FAILED", "message": str(exc)}
                        )
                translated_map[sid] = full
                # Cache store
                if self._cache and self._cache_enabled:
                    try:
                        orig = _glossary_pre(self._find_original_text(segments, sid))
                        key = self._make_cache_key(orig, src, "en", engine_key, glossary_id)
                        self._cache.put(key, full)
                    except Exception:
                        pass
        t_tr1 = time.perf_counter()

        # 4) Recombine into Markdown
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
                {"reason": "RECOMPOSE_FAILED", "message": exc.__class__.__name__},
            )
        t_rc1 = time.perf_counter()

        # 5) Assemble output with telemetry
        engine = self._engines.get(src)
        fingerprint = (
            engine.get("fingerprint")
            if engine
            else self._compute_model_fingerprint(self._models[src])
        )
        meta = dict(doc.meta or {})
        meta.setdefault("translator", {})
        meta["translator"] = {
            "engine": "marian-opus",
            "model_id": self._models[src],
            "device": self._eng.device,
            "dtype": self._eng.dtype,
            "num_beams": self._eng.num_beams,
            "length_penalty": self._eng.length_penalty,
            "no_repeat_ngram_size": self._eng.no_repeat_ngram_size,
            "fingerprint": fingerprint,
        }
        meta.setdefault("langs", {})
        meta["langs"] = {"src": src, "tgt": "en"}
        meta["segments"] = {
            "total": total,
            "cached": cached,
            "batched": batched_calls,
            "max_new_tokens": self._eng.max_new_tokens,
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
        meta["local_files_only"] = bool(self._eng.local_files_only)
        if self._eng.seed is not None:
            meta.setdefault("determinism", {})
            meta["determinism"]["seed"] = self._eng.seed

        return doc.copy_with(variant="english", lang="en", text_md=md_text_en, meta=meta)

    def capabilities(self) -> dict:
        """Return engine fingerprint and support matrix for logs.

        Returns
        -------
        dict
            Mapping describing engine identity and supported languages, e.g.::

                {
                    "name": "marian-opus",
                    "supports": {"src": ["sk","de",...], "tgt": ["en"]},
                    "deterministic": true,
                    "max_batch_size": 16,
                    "device": "cpu"
                }
        """
        return {
            "name": "marian-opus",
            "supports": {"src": sorted(list(self._models.keys())), "tgt": ["en"]},
            "deterministic": True,
            "max_batch_size": self._eng.max_batch_size,
            "device": self._eng.device,
        }

    # -----------------------------
    # Internals
    # -----------------------------

    def _translate_batch_texts(self, batch_texts: list[str], src_lang: str) -> list[str]:
        """Translate a batch of plain text segments deterministically.

        Contract
        --------
        - Uses the loaded Marian tokenizer/model for ``src_lang``.
        - Tokenizes with padding and no truncation; inputs must fit limits (the
          caller performs safe sub-splitting beforehand).
        - Calls ``generate`` with fixed beam search and sampling disabled.
        - Returns one output string per input string, aligned by index.

        Parameters
        ----------
        batch_texts : list[str]
            Plain text segments to translate; already within token limits.
        src_lang : str
            Source language code used to select the loaded engine.

        Returns
        -------
        list[str]
            Translated text strings aligned to inputs by index.

        Raises
        ------
        TranslationError
            With reason "BATCH_GENERATION_FAILED" when generation fails.
        """
        if not batch_texts:
            return []
        src = src_lang.lower()
        if src not in self._engines:
            self.load(src)
        engine = self._engines[src]
        tokenizer = engine["tokenizer"]
        model = engine["model"]

        try:
            import torch  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise TranslationError(
                "batch failed",
                {"reason": "BATCH_GENERATION_FAILED", "message": exc.__class__.__name__},
            )

        try:
            enc = tokenizer(
                batch_texts,
                return_tensors="pt",
                padding=True,
                truncation=False,  # we pre-split; do not truncate silently
            )
            # Move to device
            device = self._eng.device
            if device not in {"cpu", "cuda"}:
                device = "cpu"
            enc = {k: v.to(device) for k, v in enc.items()}

            with torch.no_grad():
                gen_kwargs = {
                    "num_beams": self._eng.num_beams,
                    "length_penalty": self._eng.length_penalty,
                    "do_sample": False,
                    "max_new_tokens": self._eng.max_new_tokens,
                }
                if self._eng.no_repeat_ngram_size is not None:
                    gen_kwargs["no_repeat_ngram_size"] = self._eng.no_repeat_ngram_size
                outputs = model.generate(
                    **enc,
                    **gen_kwargs,
                )
            decoded = tokenizer.batch_decode(outputs, skip_special_tokens=True)
        except Exception as exc:
            raise TranslationError(
                "batch failed",
                {
                    "reason": "BATCH_GENERATION_FAILED",
                    "message": str(exc.__class__.__name__),
                    "count": len(batch_texts),
                },
            )

        if len(decoded) != len(batch_texts):
            raise TranslationError(
                "batch failed",
                {
                    "reason": "BATCH_GENERATION_FAILED",
                    "message": "mismatched outputs",
                    "got": len(decoded),
                    "exp": len(batch_texts),
                },
            )

        return decoded

    def _estimate_input_budget(self, tokenizer: Any) -> int:
        """Estimate a safe input token budget to avoid truncation.

        Returns
        -------
        int
            Maximum number of input tokens permitted per text before splitting.
        """
        # Transformers tokenizers expose model_max_length; keep a margin for BOS/EOS
        max_len = getattr(tokenizer, "model_max_length", 512) or 512
        # Leave room conservatively
        return max(8, int(max_len) - 8)

    def _split_by_tokens(self, tokenizer: Any, text: str, *, max_tokens_input: int) -> list[str]:
        """Split text into subparts so each subpart fits ``max_tokens_input``.

        Strategy
        --------
        - Encodes text to count tokens. If within limit, returns [text].
        - Otherwise, splits deterministically at sentence boundaries (.!?:) or
          whitespace without breaking characters; re-checks token counts.

        Parameters
        ----------
        tokenizer : Any
            Marian tokenizer used to count tokens.
        text : str
            Input plain text segment to split.
        max_tokens_input : int
            Token ceiling per subpart for the encoder.

        Returns
        -------
        list[str]
            One or more contiguous subparts whose encoded length is within the limit.
        """
        if not text:
            return [text]
        try:
            ids = tokenizer(text, return_tensors=None, add_special_tokens=False)["input_ids"]
            if isinstance(ids, list):
                tok_len = len(ids)
            else:
                tok_len = int(len(ids))
        except Exception:
            tok_len = len(text)
        if tok_len <= max_tokens_input:
            return [text]

        # Deterministic fallback splitter
        import re

        parts: list[str] = []
        start = 0
        L = len(text)
        while start < L:
            # rough window grows with budget and some slack
            window = max_tokens_input * 4
            end = min(L, start + max(window, 16))
            # Prefer sentence boundary within window
            m = list(re.finditer(r"[.!?:]\s", text[start:end]))
            if m:
                end = start + m[-1].end()
            else:
                m2 = list(re.finditer(r"\s+", text[start:end]))
                if m2:
                    end = start + m2[-1].start()
            if end <= start:
                end = min(L, start + max(1, max_tokens_input))
            chunk = text[start:end]
            # Ensure chunk within token budget; shrink if necessary
            c_ids = tokenizer(chunk, return_tensors=None, add_special_tokens=False)["input_ids"]
            c_len = len(c_ids) if isinstance(c_ids, list) else int(len(c_ids))
            if c_len <= max_tokens_input:
                parts.append(chunk)
                start = end
                continue
            # Shrink by last space
            cut = chunk.rfind(" ")
            if cut <= 0:
                # force minimal progress
                parts.append(chunk[: max(1, len(chunk) // 2)])
                start += max(1, len(chunk) // 2)
            else:
                parts.append(chunk[:cut])
                start += cut

        # Final merge pass under budget (greedy)
        merged: list[str] = []
        for s in parts:
            if not merged:
                merged.append(s)
                continue
            cand = merged[-1] + s
            c_ids = tokenizer(cand, return_tensors=None, add_special_tokens=False)["input_ids"]
            c_len = len(c_ids) if isinstance(c_ids, list) else int(len(c_ids))
            if c_len <= max_tokens_input:
                merged[-1] = cand
            else:
                merged.append(s)
        return merged

    def _make_cache_key(
        self, text: str, src: str, tgt: str, engine_key: str, glossary_id: str | None
    ) -> str:
        """Return a deterministic cache key for one segment.

        Format: sha256("\x1f".join([text, src, tgt, engine_key, glossary_id or ""]))
        """
        h = hashlib.sha256()
        payload = "\x1f".join([text, src, tgt, engine_key, glossary_id or ""]).encode("utf-8")
        h.update(payload)
        return h.hexdigest()

    def _compute_model_fingerprint(self, model_id_or_path: str) -> str:
        """Compute a lightweight fingerprint for a Marian model reference.

        Combines the model identifier/path, device/dtype, and a short hash of
        file names, sizes, and mtimes when a local directory is provided.
        """
        try:
            p = os.path.abspath(model_id_or_path)
        except Exception:
            p = model_id_or_path
        entries: list[tuple[str, int, int]] = []
        if os.path.isdir(p):
            try:
                for name in sorted(os.listdir(p)):
                    full = os.path.join(p, name)
                    if not os.path.isfile(full):
                        continue
                    st = os.stat(full)
                    entries.append((name, int(st.st_size), int(st.st_mtime)))
            except Exception:
                entries = []
        h = hashlib.sha1()
        h.update(str(model_id_or_path).encode("utf-8"))
        h.update(self._eng.device.encode("utf-8"))
        h.update(str(self._eng.dtype).encode("utf-8"))
        for name, size, mtime in entries:
            h.update(name.encode("utf-8"))
            h.update(size.to_bytes(8, "little", signed=False))
            h.update(mtime.to_bytes(8, "little", signed=False))
        short = h.hexdigest()[:10]
        return f"{os.path.basename(str(model_id_or_path))}:{self._eng.device}:{self._eng.dtype}:{short}"

    def _find_original_text(self, segments: Any, seg_id: str) -> str:
        for s in segments:
            if getattr(s, "id", None) == seg_id:
                return getattr(s, "text", "")
        return ""
