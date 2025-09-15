from __future__ import annotations

"""
English detector based on fastText LID model (offline, deterministic).

This adapter implements EnglishDetectPort by loading the same fastText LID 176
model used for language detection and deriving an English confidence score from
model outputs. It performs the same Markdown cleaning and sampling strategy as
the FastTextLangId adapter and returns a calibrated confidence in [0,1].

Notes:
- No heuristic token rules are used; the score comes directly from the model's
  predicted probability for the 'en' label when present in the top-k.
- If 'en' is not present among top-k results, a small floor value is returned.
- The adapter is thread-safe for concurrent calls and deterministic.
"""

import os
import threading
import unicodedata
import re

from preprocessing.domain.ports import EnglishDetectPort, LanguageDetectError


class FastTextEnglishDetector(EnglishDetectPort):
    def __init__(self, model_path: str, *, max_chars: int = 3000, min_chars: int = 32) -> None:
        if not model_path:
            raise LanguageDetectError("MODEL_LOAD_FAILED", {"message": "empty model_path"})
        self._model_path = model_path
        self._max_chars = int(max_chars)
        self._min_chars = int(min_chars)
        self._model = None
        self._loaded = False
        self._lock = threading.RLock()

    def load(self) -> None:
        with self._lock:
            if self._loaded and self._model is not None:
                return
            try:
                import fasttext  # type: ignore
            except Exception as e:
                raise LanguageDetectError(
                    "MODEL_LOAD_FAILED",
                    {"message": "fasttext module not available", "exc_type": e.__class__.__name__},
                )
            try:
                self._model = fasttext.load_model(self._model_path)  # type: ignore[attr-defined]
                self._loaded = True
            except Exception as e:
                raise LanguageDetectError(
                    "MODEL_LOAD_FAILED",
                    {
                        "message": "failed to load fastText model",
                        "model_path": os.path.abspath(self._model_path),
                        "exc_type": e.__class__.__name__,
                    },
                )

    def english_confidence(self, text: str) -> float:  # type: ignore[override]
        if not self._loaded or self._model is None:
            self.load()
        cleaned = self._clean(text)
        sampled = self._sample(cleaned, self._max_chars)
        sanitized = self._sanitize(sampled)
        if len(sanitized) < self._min_chars:
            # For very short inputs, provide a conservative floor
            return 0.20
        try:
            labels, scores = self._model.predict(sanitized, k=10)  # type: ignore[attr-defined]
        except Exception as e:
            raise LanguageDetectError(
                "PREDICT_FAILED",
                {
                    "message": "fastText predict failed",
                    "exc_type": e.__class__.__name__,
                    "exc_msg": str(e),
                },
            )
        # Map labels to raw codes and look for English
        en_score = 0.0
        for lab, sc in zip(labels, scores):
            lab_s = str(lab)
            if lab_s.endswith("__en") or lab_s.endswith("__eng") or lab_s.endswith("__en-us"):
                en_score = float(sc)
                break
        # Calibrate to [0,1] with mild emphasis on separation
        if en_score < 0.0:
            en_score = 0.0
        if en_score > 1.0:
            en_score = 1.0
        # Simple monotonic mapping: sqrt to spread mid-range
        conf = en_score**0.5
        # Floor to avoid reporting zero for non-top-k presence
        conf = max(0.05 if en_score == 0.0 else 0.20, min(1.0, conf))
        return conf

    @staticmethod
    def _clean(text: str) -> str:
        # Lightweight cleaning: collapse whitespace; avoiding heavy regex to keep import-light
        if not text:
            return ""
        s = text.replace("\r\n", "\n").replace("\r", "\n")
        # Remove simple fenced code blocks hints to reduce noise deterministically
        # Keep it simple to avoid dependency
        s = s.replace("```", " ")
        return " ".join(s.split())

    @staticmethod
    def _sanitize(s: str) -> str:
        """Remove problematic control/surrogate/other unicode characters."""
        if not s:
            return s
        s = unicodedata.normalize("NFC", s)
        out = []
        for ch in s:
            cat = unicodedata.category(ch)
            if cat and cat[0] == "C":
                out.append(" ")
            else:
                out.append(ch)
        s = "".join(out)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    @staticmethod
    def _sample(cleaned: str, max_chars: int) -> str:
        if max_chars <= 0 or len(cleaned) <= max_chars:
            return cleaned
        sep = " "
        head = cleaned[: max_chars // 2]
        tail = cleaned[-(max_chars - len(head) - len(sep)) :]
        return (head + sep + tail)[:max_chars]


__all__ = ["FastTextEnglishDetector"]
