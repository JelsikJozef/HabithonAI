from __future__ import annotations

from dataclasses import replace
import hashlib
import math
import re
from typing import Callable, Optional

from ...domain.models import ParsedDocument
from ...domain.ports import EnrichmentPort

# --- fastText language ID support (lazy load + on-demand model download) ---
from pathlib import Path
import os
import threading

_FT_MODEL_LOCK = threading.Lock()
_FT_MODEL = None  # type: ignore[var-annotated]
_FT_MODEL_ERR: Optional[str] = None

_FT_DEFAULT_URL = "https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.ftz"
_FT_DEFAULT_NAME = "lid.176.ftz"


def _cache_dir() -> Path:
    # Prefer XDG cache if set, else ~/.cache
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "habithon" / "fasttext"


def _ensure_fasttext_model_path() -> Optional[Path]:
    """Return path to lid.176.ftz, downloading it if missing.

    Honors PREPROCESSING_FASTTEXT_MODEL if set to an existing file path.
    """
    # 1) Honor explicit override
    override = os.environ.get("PREPROCESSING_FASTTEXT_MODEL")
    if override:
        p = Path(override).expanduser()
        if p.is_file():
            return p
    # 2) Default cache location
    target_dir = _cache_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / _FT_DEFAULT_NAME
    if path.is_file() and path.stat().st_size > 0:
        return path
    # 3) Attempt download (best-effort)
    try:
        import urllib.request  # stdlib
        tmp = path.with_suffix(path.suffix + ".part")
        with urllib.request.urlopen(_FT_DEFAULT_URL, timeout=30) as resp:  # nosec B310
            data = resp.read()
        tmp.write_bytes(data)
        tmp.replace(path)
        return path
    except Exception as e:  # best-effort; no model -> return None
        global _FT_MODEL_ERR
        _FT_MODEL_ERR = f"fastText model download failed: {e}"
        return None


def _get_fasttext_model():  # -> Optional[fasttext.FastText]
    global _FT_MODEL, _FT_MODEL_ERR
    if _FT_MODEL is not None or _FT_MODEL_ERR is not None:
        return _FT_MODEL
    with _FT_MODEL_LOCK:
        if _FT_MODEL is not None or _FT_MODEL_ERR is not None:
            return _FT_MODEL
        try:
            # Try importing either official fasttext or wheel build
            import fasttext  # type: ignore
        except Exception as e:
            _FT_MODEL_ERR = f"fastText import failed: {e}"
            return None
        model_path = _ensure_fasttext_model_path()
        if not model_path:
            return None
        try:
            _FT_MODEL = fasttext.load_model(str(model_path))  # type: ignore[attr-defined]
        except Exception as e:
            _FT_MODEL_ERR = f"fastText load_model failed: {e}"
            _FT_MODEL = None
        return _FT_MODEL


class MetadataAnnotator(EnrichmentPort):
    """Heuristic metadata annotation.

    - language: via provided lang_detector(text)->str or fastText lid.176 model if available.
    - hash: SHA-256 of text content as hex string.
    - tokens: via provided token_counter(text)->int or whitespace token count.
    - counts: chars, words; reading_time_sec estimated at ~200 wpm.
    - metadata: merged with {"enriched": True} and counts.
    """

    def __init__(
        self,
        lang_detector: Optional[Callable[[str], str]] | None = None,
        token_counter: Optional[Callable[[str], int]] | None = None,
        *,
        min_lang_confidence: float = 0.30,
    ) -> None:
        self._lang_detector = lang_detector
        self._token_counter = token_counter
        # Only accept sane bounds for threshold
        self._min_lang_conf = min(1.0, max(0.0, float(min_lang_confidence)))

    def enrich(self, doc: ParsedDocument) -> ParsedDocument:
        text = doc.text or ""
        # language
        language = self._detect_language(text)
        # hash
        content_hash = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
        # tokens/words
        tokens = self._count_tokens(text)
        # words (alphanumeric sequences)
        words = len(re.findall(r"\w+", text, flags=re.UNICODE))
        # chars
        chars = len(text)
        # reading time (approx 200 wpm)
        reading_time_sec = int(math.ceil(words / (200 / 60))) if words else 0
        # merge metadata
        meta = dict(doc.metadata)
        meta.update({
            "enriched": True,
            "counts": {"chars": chars, "words": words},
            "reading_time_sec": reading_time_sec,
        })
        return replace(
            doc,
            language=language,
            hash=content_hash,
            tokens=tokens,
            metadata=meta,
        )

    def _detect_language(self, text: str) -> Optional[str]:
        # 1) External override if provided by caller
        if self._lang_detector is not None:
            try:
                lang = self._lang_detector(text)
                if isinstance(lang, str) and lang:
                    return lang
            except Exception:
                pass
        # Normalize text
        s = (text or "").strip()
        if not s:
            return None
        # 2) fastText detection (lid.176) if available
        model = _get_fasttext_model()
        if model is not None:
            try:
                # fastText expects lines; keep it short to avoid huge inputs
                sample = s.replace("\n", " ")
                if len(sample) > 4000:
                    sample = sample[:4000]
                labels, probs = model.predict(sample, k=1)  # type: ignore[call-arg]
                if labels and probs:
                    label = str(labels[0])
                    prob = float(probs[0])
                    # fastText labels look like __label__en
                    if label.startswith("__label__") and prob >= self._min_lang_conf:
                        code = label.replace("__label__", "", 1)
                        return code or None
                    # Very low confidence -> return None to trigger downstream fallback
                    return None
            except Exception:
                # If fastText fails at runtime, do not crash enrichment
                return None
        # 3) No detector available -> return None so downstream can fallback
        return None

    def _count_tokens(self, text: str) -> int:
        if self._token_counter is not None:
            try:
                n = self._token_counter(text)
                if isinstance(n, int) and n >= 0:
                    return n
            except Exception:
                pass
        return len(text.split())

# Backward-compatible alias
MetadataEnricher = MetadataAnnotator
