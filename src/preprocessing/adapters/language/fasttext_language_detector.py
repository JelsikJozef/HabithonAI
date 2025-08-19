from __future__ import annotations

from pathlib import Path
from typing import Optional, Union
import os


class FastTextLanguageDetector:
    """Language detector powered by fastText lid.176 model.

    Contract:
    - __init__(model_path: Optional[str|Path] = None, min_confidence: float = 0.5, max_chars: int = 4000)
      Loads the model from a configurable path or a default cached location.
    - detect(text: str) -> Optional[str]
      Returns ISO 639-1 code like "en", "sk", "de" or None if confidence is below threshold.
    """

    def __init__(
        self,
        model_path: Optional[Union[str, Path]] = None,
        *,
        min_confidence: float = 0.5,
        max_chars: int = 4000,
    ) -> None:
        if not (0.0 <= float(min_confidence) <= 1.0):
            raise ValueError("min_confidence must be in [0.0, 1.0]")
        self._min_conf = float(min_confidence)
        self._max_chars = int(max_chars) if max_chars and max_chars > 0 else 4000
        # Lazy import to avoid hard dependency unless used
        try:
            import fasttext  # type: ignore
        except Exception as e:
            raise RuntimeError(f"fastText import failed: {e}") from e
        self._fasttext = fasttext
        # Resolve model path and load
        path = self._resolve_model_path(model_path)
        try:
            self._model = self._fasttext.load_model(str(path))  # type: ignore[attr-defined]
        except Exception as e:
            raise RuntimeError(f"fastText load_model failed for '{path}': {e}") from e

    def _resolve_model_path(self, model_path: Optional[Union[str, Path]]) -> Path:
        # 1) Explicit argument
        if model_path is not None:
            p = Path(model_path).expanduser()
            if not p.is_file():
                raise FileNotFoundError(f"fastText model not found at {p}")
            return p
        # 2) Environment override
        env = os.environ.get("PREPROCESSING_FASTTEXT_MODEL")
        if env:
            p = Path(env).expanduser()
            if p.is_file():
                return p
        # 3) Default cache location (attempt download if missing)
        cache_path = self._default_cache_path()
        if cache_path.is_file() and cache_path.stat().st_size > 0:
            return cache_path
        # Try download into cache
        self._download_model(cache_path)
        if not cache_path.is_file() or cache_path.stat().st_size == 0:
            raise FileNotFoundError("fastText lid.176.ftz not available after attempted download")
        return cache_path

    def _default_cache_path(self) -> Path:
        xdg = os.environ.get("XDG_CACHE_HOME")
        base = Path(xdg) if xdg else Path.home() / ".cache"
        target_dir = base / "habithon" / "fasttext"
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir / "lid.176.ftz"

    def _download_model(self, target: Path) -> None:
        url = "https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.ftz"
        try:
            import urllib.request  # stdlib
            tmp = target.with_suffix(target.suffix + ".part")
            with urllib.request.urlopen(url, timeout=30) as resp:  # nosec B310
                data = resp.read()
            tmp.write_bytes(data)
            tmp.replace(target)
        except Exception as e:
            # Best-effort: leave a clear message for the caller at init time
            raise RuntimeError(f"fastText model download failed: {e}") from e

    def detect(self, text: str) -> Optional[str]:
        s = (text or "").strip()
        if not s:
            return None
        # Preprocess: normalize whitespace and truncate (preserve diacritics)
        sample = " ".join(s.split())
        if len(sample) > self._max_chars:
            sample = sample[: self._max_chars]
        # Primary: top-1
        try:
            labels, probs = self._model.predict(sample, k=1)  # type: ignore[call-arg]
            if labels and probs:
                label = str(labels[0])
                prob = float(probs[0])
                if label.startswith("__label__"):
                    code = label.replace("__label__", "", 1).strip().lower()
                    if prob >= self._min_conf:
                        return code or None
        except Exception:
            # Do not bail out; continue with fallbacks below
            pass
        # Optional: low-confidence fallback using top-3 candidates (still fastText-only)
        try:
            labels3, probs3 = self._model.predict(sample, k=3)  # type: ignore[call-arg]
            candidates: list[tuple[str, float]] = []
            for i in range(min(len(labels3 or []), len(probs3 or []))):
                lab = str(labels3[i])
                if not lab.startswith("__label__"):
                    continue
                code = lab.replace("__label__", "", 1).strip().lower()
                try:
                    p = float(probs3[i])
                except Exception:
                    continue
                candidates.append((code, p))
            relaxed = max(0.2, self._min_conf * 0.7)
            above = [(c, p) for c, p in candidates if p >= relaxed]
            if above:
                above.sort(key=lambda x: x[1], reverse=True)
                return above[0][0]
        except Exception:
            pass
        return None

