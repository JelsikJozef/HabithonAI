from __future__ import annotations

"""FastText-based offline language identifier.

Responsibilities
================
- Load a local fastText LID model from disk (no network calls).
- Pre-process Markdown to reduce noise (strip code blocks, inline code, URLs, HTML),
  while keeping human-readable text such as headings, paragraphs, list items, and
  link labels/alt text.
- Provide deterministic predictions across runs given the same input, model, and
  configuration. Sampling is deterministic and does not rely on global RNG.
- Respect optional hints/candidates to bias or validate outputs.
- Never leak vendor exceptions; translate to LanguageDetectError.

Public API
==========
Class FastTextLangId implements LanguageDetectPort with methods:
- __init__(model_path, **cfg): configure and optionally prepare for loading.
- load(): idempotent, thread-safe model loader.
- detect(text, hints=None, *, context=None) -> tuple[str, float]:
  return (lang_code, confidence) where lang_code is lowercase ISO-like code and
  confidence ∈ [0, 1], calibrated.
- capabilities() -> dict: small fingerprint of the engine and config.
- close(): idempotent release of resources.

Parameters (constructor)
------------------------
- model_path (str, required): path to a fastText language ID model (e.g., lid.176.bin).
- max_chars (int, default 5000): upper bound of characters analyzed after preprocessing; if
  the cleaned text exceeds this, the adapter samples from head + tail deterministically.
- min_chars (int, default 64): minimum characters required for confident inference; below
  this, the adapter returns a best-effort language with a low confidence floor.
- candidates (list[str] | None, default None): hard whitelist restricting outputs to these
  lowercased codes; when provided, predictions outside are penalized or re-ranked if close
  alternatives exist.
- blacklist (list[str] | None, default None): disallowed languages to avoid returning.
- preprocess (dict):
    - strip_code_blocks (bool, default True)
    - strip_inline_code (bool, default True)
    - strip_urls_html (bool, default True)
    - collapse_whitespace (bool, default True)
- calibration (dict):
    - score_mapping (str, default "piecewise_v1"): mapping of raw FT scores to [0,1],
      supported: "identity", "piecewise_v1".
    - min_confidence_report (float, default 0.20): floor applied to extremely short/noisy inputs.
- seed (int | None, default None): reserved for deterministic sampling decisions; sampling
  here is deterministic without RNG, but the seed is accepted for compatibility.
- lang_map (dict[str, str] | None): mapping from fastText labels to lowercased ISO codes; if
  not provided, a default mapping derived from the label suffix is used.

Returns (detect)
----------------
- tuple[str, float]: (lang_code, confidence) where lang_code is a lowercase two-letter
  ISO-like code whenever possible (e.g., "sk", "de", "cs", "pl", "hu", "en").
  Confidence is a calibrated float in [0, 1] that is monotonic with the raw model score
  and comparable across documents; it includes penalties/floors based on input length
  and candidate constraints.

Errors
------
- LanguageDetectError("MODEL_LOAD_FAILED", details) when the model cannot be imported/loaded.
- LanguageDetectError("PREDICT_FAILED", details) when prediction fails unexpectedly.
- On short/noisy input, no exception is raised; a best-effort language with a low confidence
  floor is returned.
"""

import os
import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from preprocessing.domain.ports import LanguageDetectError

_Label = str
_Code = str


@dataclass
class _CalibrationCfg:
    """Internal calibration configuration.

    Attributes:
        score_mapping: Name of the raw-score-to-confidence mapping. Supported: "identity",
            "piecewise_v1" (default). The mapping must be monotonic.
        min_confidence_report: Lower bound for reported confidence on extremely short/noisy
            inputs, in [0, 1]. Default 0.20.
    """

    score_mapping: str = "identity"
    min_confidence_report: float = 0.20


class FastTextLangId:  # no direct inheritance from Protocol to avoid strict signature coupling
    """Offline, deterministic language detector using fastText LID 176.

    This adapter implements LanguageDetectPort. It loads a local fastText model, preprocesses
    Markdown to remove noise, performs deterministic sampling, and returns a lowercase language
    code with a calibrated confidence in [0, 1]. Hints (candidate languages) can constrain
    or bias the decision. Vendor exceptions are translated into LanguageDetectError.

    Thread-safety:
        A single model instance is kept per adapter instance. Load and predict are guarded by
        a lock for safety with the Python fastText bindings.

    Determinism:
        Given the same input, model file, and configuration, detect() returns the same result.
        Sampling is head+tail deterministic. No global RNG is used.
    """

    # Reasonable defaults tuned for Markdown documents
    _DEFAULT_MAX_CHARS = 5000
    _DEFAULT_MIN_CHARS = 64

    def __init__(
        self,
        model_path: str,
        **cfg: Any,
    ) -> None:
        """Create a FastText-based language detector.

        Parameters:
            model_path: Absolute or relative path to a fastText language model (e.g., lid.176.bin).
            max_chars: Optional int; upper bound of characters used for inference after preprocessing.
            min_chars: Optional int; minimum characters required before trusting the model score.
            candidates: Optional list[str]; whitelist of allowed language codes to prefer/restrict.
            blacklist: Optional list[str]; disallowed language codes.
            preprocess: Optional dict of preprocessing flags: strip_code_blocks, strip_inline_code,
                strip_urls_html, collapse_whitespace. Defaults to all True.
            calibration: Optional dict with keys: score_mapping ("identity"|"piecewise_v1"),
                min_confidence_report (float in [0,1]).
            seed: Optional int; accepted but unused (sampling is deterministic without RNG).
            lang_map: Optional dict mapping fastText labels (e.g., "__label__sk") to lowercase ISO codes.

        Raises:
            LanguageDetectError: If model_path is obviously invalid (empty) or cannot be resolved
                during deferred load (raised in load()).
        """
        self._model_path = str(model_path)
        if not self._model_path:
            raise LanguageDetectError("MODEL_LOAD_FAILED", details={"message": "empty model_path"})

        # Core config
        self._max_chars: int = int(
            cfg.get("max_chars", self._DEFAULT_MAX_CHARS) or self._DEFAULT_MAX_CHARS
        )
        self._min_chars: int = int(
            cfg.get("min_chars", self._DEFAULT_MIN_CHARS) or self._DEFAULT_MIN_CHARS
        )
        self._candidates_cfg: Sequence[str] | None = [
            c.lower() for c in (cfg.get("candidates") or [])
        ] or None
        self._blacklist_cfg: set[str] = set([b.lower() for b in (cfg.get("blacklist") or [])])

        # Preprocess flags
        pp = dict(cfg.get("preprocess", {}) or {})
        self._strip_code_blocks: bool = bool(pp.get("strip_code_blocks", True))
        self._strip_inline_code: bool = bool(pp.get("strip_inline_code", True))
        self._strip_urls_html: bool = bool(pp.get("strip_urls_html", True))
        self._collapse_ws: bool = bool(pp.get("collapse_whitespace", True))

        # Calibration cfg
        cal = dict(cfg.get("calibration", {}) or {})
        self._calibration = _CalibrationCfg(
            score_mapping=str(cal.get("score_mapping", "piecewise_v1") or "piecewise_v1"),
            min_confidence_report=float(cal.get("min_confidence_report", 0.20) or 0.20),
        )

        # Seed: accepted for forward-compatibility
        self._seed: int | None = cfg.get("seed")

        # Label -> code mapping
        default_lang_map: dict[str, str] = {
            # Identity mapping via suffix, but include a few canonicalizations if needed.
            # e.g., "__label__zh-cn" -> "zh", "__label__pt-br" -> "pt".
        }
        # Allow caller to pass explicit map; otherwise, use suffix parsing fallback.
        self._lang_map: dict[str, str] = dict(cfg.get("lang_map") or default_lang_map)

        # Internal state
        self._model = None  # loaded lazily
        self._loaded = False
        self._lock = threading.RLock()

        # Last-run lightweight telemetry (optional, not used by public API)
        self._last_stats: dict[str, Any] | None = None

    # -----------------------------
    # Lifecycle
    # -----------------------------

    def load(self) -> None:
        """Load the fastText model from disk.

        Behavior:
            - Idempotent and thread-safe; multiple calls are safe.
            - Translates ImportError/IOError into LanguageDetectError("MODEL_LOAD_FAILED").

        Raises:
            LanguageDetectError: When fastText is not installed or the model file cannot be loaded.
        """
        with self._lock:
            if self._loaded and self._model is not None:
                return
            # Validate path early for clearer error messages
            model_path = self._model_path
            try:
                import fasttext  # type: ignore
            except Exception as e:  # pragma: no cover - environment dependent
                raise LanguageDetectError(
                    "MODEL_LOAD_FAILED",
                    details={
                        "message": "fasttext module not available",
                        "exc_type": type(e).__name__,
                    },
                )
            try:
                # fastText expects a filesystem path; let it raise if unreadable
                self._model = fasttext.load_model(model_path)  # type: ignore[attr-defined]
                self._loaded = True
            except Exception as e:  # pragma: no cover - depends on runtime model availability
                raise LanguageDetectError(
                    "MODEL_LOAD_FAILED",
                    details={
                        "message": "failed to load fastText model",
                        "model_path": os.path.abspath(model_path),
                        "exc_type": type(e).__name__,
                    },
                )

    # -----------------------------
    # Public API
    # -----------------------------

    def detect(
        self,
        text: str,
        hints: dict[str, Any] | None = None,
        *,
        context: dict[str, Any] | None = None,
    ) -> tuple[str, float]:
        """Detect the primary language of Markdown text.

        Parameters:
            text: Full Markdown body (LF newlines, UTF-8) which may include code fences, inline code,
                URLs, HTML, tables, and mixed content. This function does not mutate the input.
            hints: Optional soft hints to guide or constrain detection. Recognized keys:
                - candidates: list[str] of allowed/preferred language codes (lowercased ISO-like)
                  to bias selection or restrict outputs.
                - path: optional file path (string) used only for logging/telemetry, not for logic.
            context: Optional opaque dict for telemetry (doc_id, path_rel, run_id). Not used for logic.

        Processing steps:
            1. Normalize/clean Markdown per configuration by removing code blocks, inline code,
               raw HTML, URLs/emails, and keeping human text such as headings and link labels.
            2. Sample deterministically (head + tail) if cleaned length exceeds max_chars.
            3. If cleaned length < min_chars, return a best-effort guess with low confidence floor.
            4. Predict with fastText, map label to lowercase ISO-like code, and apply candidate
               constraints (hints/config). If the top prediction is outside candidates, prefer the
               best-scoring candidate if available; otherwise penalize confidence.
            5. Calibrate the raw score to [0, 1], apply floors/penalties, and return.

        Returns:
            tuple[str, float]: (lang_code, confidence) where lang_code is a lowercase code like "en"
            or "sk" and confidence is a calibrated float in [0, 1].

        Raises:
            LanguageDetectError: Only on unrecoverable runtime failures (e.g., model load/predict issues).
        """
        # Lightweight telemetry container
        stats: dict[str, Any] = {
            "chars_in": len(text or ""),
            "chars_used": 0,
            "had_code_blocks": False,
            "confidence": 0.0,
        }

        # Ensure the model is ready
        if not self._loaded or self._model is None:
            self.load()

        # Preprocess Markdown to retain human text
        cleaned, had_code = self._preprocess_md(text)
        stats["had_code_blocks"] = had_code

        # Sampling (deterministic head+tail)
        sampled = self._sample_cleaned(cleaned, self._max_chars)
        stats["chars_used"] = len(sampled)

        # Short/noisy guard rail
        if len(sampled) < self._min_chars:
            guess = self._best_effort_guess(sampled, hints)
            conf = max(self._calibration.min_confidence_report, 0.0)
            stats["confidence"] = conf
            self._last_stats = stats
            return guess, conf

        # Prediction
        try:
            # Request top-k to allow candidate re-ranking; small k for speed
            labels, scores = self._predict(sampled, top_k=5)
        except LanguageDetectError:
            raise
        except Exception as e:  # pragma: no cover - depends on vendor runtime
            raise LanguageDetectError(
                "PREDICT_FAILED",
                details={"message": "fastText predict failed", "exc_type": type(e).__name__},
            )

        # Convert labels to codes and pair with scores
        ranked: list[tuple[_Code, float]] = []
        for lab, sc in zip(labels, scores):
            code = self._map_label(lab)
            if not code:
                continue
            if code in self._blacklist_cfg:
                continue
            ranked.append((code, float(sc)))
        if not ranked:
            # Fallback: avoid raising for empty mapping
            guess = self._best_effort_guess(sampled, hints)
            conf = self._calibrate(0.0, len(sampled), within_candidates=False)
            stats["confidence"] = conf
            self._last_stats = stats
            return guess, conf

        # Apply candidate constraints (config + hints)
        hint_candidates = [c.lower() for c in (hints or {}).get("candidates", [])] if hints else []
        all_candidates = self._merge_candidates(hint_candidates)
        within_candidates = True
        chosen_code, raw_score = ranked[0]
        if all_candidates:
            # Choose the highest-scoring item that is in candidates if any exist
            cand_ranked = [(c, s) for (c, s) in ranked if c in all_candidates]
            if cand_ranked:
                chosen_code, raw_score = cand_ranked[0]
            else:
                # No candidate among top-k; keep the original top prediction but mark as outside
                within_candidates = False

        # Final confidence with calibration
        confidence = self._calibrate(raw_score, len(sampled), within_candidates=within_candidates)
        stats["confidence"] = confidence
        self._last_stats = stats
        return chosen_code, confidence

    def capabilities(self) -> dict[str, Any]:
        """Return engine fingerprint and supported capabilities.

        Returns:
            dict with keys:
                - name: Engine identifier string (e.g., "fasttext-lid176").
                - version: Model fingerprint (e.g., file mtime or a short string if unavailable).
                - supports: List of sample/known language codes (lowercase) this adapter returns.
                - max_chars: Upper bound of characters analyzed per call.
                - deterministic: Always True.
        """
        try:
            mtime = os.path.getmtime(self._model_path)
            version = f"mtime-{int(mtime)}"
        except Exception:
            version = "unknown"
        # If explicit lang_map provided, expose its values; else provide a typical FT LID set sample
        supports = sorted(
            {v for v in self._lang_map.values()}
            or {
                "en",
                "sk",
                "de",
                "cs",
                "pl",
                "hu",
                "fr",
                "es",
                "it",
                "pt",
                "nl",
                "da",
                "sv",
                "no",
            }
        )
        return {
            "name": "fasttext-lid176",
            "version": version,
            "supports": supports,
            "max_chars": int(self._max_chars),
            "deterministic": True,
        }

    def close(self) -> None:
        """Release model resources if needed.

        Behavior:
            - Idempotent; safe to call multiple times.
            - After close(), detect() will re-load the model on demand.
        """
        with self._lock:
            self._model = None
            self._loaded = False

    # -----------------------------
    # Internals: preprocessing and sampling
    # -----------------------------

    _RE_FENCE = re.compile(r"(?s)\n?\s*(```|~~~)[^\n]*\n.*?\n\s*\1\s*\n?")
    _RE_INDENTED_CODE = re.compile(r"(?m)^(?: {4}|\t).*$")
    _RE_INLINE_CODE = re.compile(r"`[^`\n]+`")
    _RE_HTML_TAG = re.compile(r"<[^>]+>")
    _RE_URL = re.compile(r"\b(?:https?|ftp)://[\w\-._~:/?#\[\]@!$&'()*+,;=%]+", re.IGNORECASE)
    _RE_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
    _RE_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    _RE_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
    _RE_WS = re.compile(r"\s+")

    def _preprocess_md(self, text: str) -> tuple[str, bool]:
        """Strip Markdown noise and keep human-readable text.

        Parameters:
            text: Input Markdown string which may contain code fences, inline code, links, images,
                tables, HTML, and other Markdown constructs.

        Returns:
            tuple[str, bool]: (cleaned_text, had_code_blocks) where cleaned_text is the processed
            text suitable for language detection and had_code_blocks indicates whether code blocks
            were detected and removed.
        """
        if not text:
            return "", False

        s = "\n" + text  # Leading newline simplifies some fence patterns
        had_code = False

        if self._strip_code_blocks:
            # Remove fenced code blocks (``` or ~~~) including info strings
            before = len(s)
            s = self._RE_FENCE.sub("\n", s)
            if len(s) != before:
                had_code = True
            # Remove indented code blocks
            before = len(s)
            s = self._RE_INDENTED_CODE.sub("", s)
            if len(s) != before:
                had_code = True

        # Keep link labels, drop destinations: [label](url) -> label
        def _keep_label(m: re.Match[str]) -> str:
            return m.group(1)

        s = self._RE_MD_LINK.sub(_keep_label, s)

        # Keep image alt text: ![alt](url) -> alt
        def _keep_alt(m: re.Match[str]) -> str:
            return m.group(1) or ""

        s = self._RE_MD_IMAGE.sub(_keep_alt, s)

        if self._strip_inline_code:
            s = self._RE_INLINE_CODE.sub(" ", s)

        if self._strip_urls_html:
            s = self._RE_URL.sub(" ", s)
            s = self._RE_EMAIL.sub(" ", s)
            s = self._RE_HTML_TAG.sub(" ", s)

        if self._collapse_ws:
            s = self._RE_WS.sub(" ", s)
            s = s.strip()
        else:
            s = s.strip("\n ")

        return s, had_code

    def _sample_cleaned(self, cleaned: str, max_chars: int) -> str:
        """Deterministically sample cleaned text to a maximum length.

        Parameters:
            cleaned: Preprocessed text from _preprocess_md.
            max_chars: Upper limit of characters considered for detection.

        Returns:
            str: Sampled text. If len(cleaned) <= max_chars, returns cleaned unchanged. Otherwise,
            returns head + ellipsis + tail to reach at most max_chars characters deterministically.
        """
        if max_chars <= 0 or len(cleaned) <= max_chars:
            return cleaned
        # Reserve 1 space as a separator between head and tail when sampling
        sep = " "
        budget = max_chars
        head_budget = budget // 2
        tail_budget = budget - head_budget - len(sep)
        if tail_budget < 0:
            # Edge case when max_chars is 1: just return first char
            return cleaned[:budget]
        head = cleaned[:head_budget]
        tail = cleaned[-tail_budget:]
        out = head + sep + tail
        if len(out) > max_chars:
            out = out[:max_chars]
        return out

    # -----------------------------
    # Internals: prediction and calibration
    # -----------------------------

    def _predict(self, text: str, top_k: int = 1) -> tuple[list[_Label], list[float]]:
        """Call the vendor model predict in a guarded, deterministic manner.

        Parameters:
            text: Cleaned and sampled text.
            top_k: Number of top labels requested from the model.

        Returns:
            tuple[list[str], list[float]]: Parallel arrays of labels and raw scores as returned by fastText.

        Raises:
            LanguageDetectError: When the model is not loaded or prediction fails.
        """
        if not self._loaded or self._model is None:
            self.load()
        try:
            with self._lock:
                # vendor returns (labels, scores) where labels are strings like "__label__en"
                labels, scores = self._model.predict(text, k=max(1, int(top_k)))  # type: ignore[attr-defined]
        except Exception as e:  # pragma: no cover - vendor specific
            raise LanguageDetectError(
                "PREDICT_FAILED",
                details={"message": "fastText predict exception", "exc_type": type(e).__name__},
            )
        # fastText may return numpy arrays; ensure Python lists
        lbls = [str(x) for x in (labels or [])]
        scs = [float(x) for x in (scores or [])]
        return lbls, scs

    def _map_label(self, label: _Label) -> _Code:
        """Map a fastText label (e.g., "__label__sk") to a lowercase ISO-like code.

        Parameters:
            label: Vendor label string, typically prefixed with "__label__".

        Returns:
            str: Lowercase ISO-like language code (e.g., "sk"). Empty string if mapping fails.
        """
        if not label:
            return ""
        if label in self._lang_map:
            return self._lang_map[label].lower()
        # Fallback: strip prefix and canonicalize common variants
        l = label
        if l.startswith("__label__"):
            l = l[len("__label__") :]
        l = l.replace("_", "-").lower()
        # Canonicalize some regional tags to base
        if l.startswith("pt-"):
            return "pt"
        if l.startswith("zh-"):
            return "zh"
        if l.startswith("sr-"):
            return "sr"
        # Reduce to base code if longer than 2
        if len(l) > 2:
            l = l.split("-")[0]
        return l

    def _merge_candidates(self, hint_candidates: Sequence[str]) -> set[str]:
        """Merge configured and hint-provided candidates into a lowercase set."""
        merged: set[str] = set([c.lower() for c in hint_candidates or []])
        if self._candidates_cfg:
            merged.update([c.lower() for c in self._candidates_cfg])
        return merged

    def _calibrate(self, raw: float, used_len: int, *, within_candidates: bool) -> float:
        """Map a raw fastText score into a calibrated [0, 1] confidence.

        Parameters:
            raw: Raw probability-like score from fastText for the chosen label.
            used_len: Characters used for inference after preprocessing and sampling.
            within_candidates: Whether the chosen label is within the provided/merged candidates
                (hints or config). If False, a penalty is applied.

        Returns:
            float: Calibrated confidence in [0, 1], monotonic in the raw score, with floors applied
            for short inputs.
        """
        r = max(0.0, min(1.0, float(raw)))
        if self._calibration.score_mapping == "piecewise_v1":
            # Simple monotonic alternative: emphasize separation by square root mapping
            conf = r**0.5
        else:  # identity or unknown
            conf = r
        # Apply length-based floor and candidate penalty
        if used_len < self._min_chars:
            conf = min(conf, self._calibration.min_confidence_report)
        if not within_candidates:
            conf *= 0.6  # penalize when outside allowed set but still return the prediction
        # Bound to [0,1] and floor for reporting
        conf = max(self._calibration.min_confidence_report, min(1.0, conf))
        return conf

    def _best_effort_guess(self, cleaned: str, hints: dict[str, Any] | None) -> str:
        """Guess a reasonable language when text is too short/noisy.

        Priority:
            1. First hint candidate if provided.
            2. Simple diacritic heuristics for a few languages of interest.
            3. Default to "en".
        """
        # 1) Hints
        if hints and hints.get("candidates"):
            cands = [str(c).lower() for c in hints["candidates"] or []]
            if cands:
                return cands[0]
        s = cleaned or ""
        # 2) Heuristics by character set
        if re.search(r"[äöüß]", s):
            return "de"
        if re.search(r"[áäéíóôúýčďľňŕšťž]", s, flags=re.IGNORECASE):  # sk/cs diacritics
            # If candidates restrict to cs vs sk, prefer if present
            if hints and hints.get("candidates"):
                cset = {c.lower() for c in hints["candidates"]}
                if "sk" in cset:
                    return "sk"
                if "cs" in cset:
                    return "cs"
            return "sk"
        if re.search(r"[ąćęłńóśźż]", s, flags=re.IGNORECASE):
            return "pl"
        if re.search(r"[őű]", s, flags=re.IGNORECASE):
            return "hu"
        # 3) Default
        return "en"


__all__ = ["FastTextLangId"]
