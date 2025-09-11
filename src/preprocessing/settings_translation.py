"""
Translation settings (static, import-light) for the preprocessing module.

This module exposes a single, immutable settings mapping `TRANSLATION` that
configures the English-translation step. It includes engine selection, model
locations, decoding parameters, segmentation options, language detection,
cache/glossary toggles, IO policy, logging/telemetry, and schema metadata.

Environment overrides (read at import):
- HABITHON_TRANSLATION_ENGINE           -> engine selection ("ct2_nllb"|"marian_opus")
- HABITHON_CT2_MODEL_DIR                -> path to CT2/NLLB model directory
- HABITHON_CT2_DEVICE                   -> "cpu"|"cuda"
- HABITHON_CT2_COMPUTE_TYPE             -> e.g., "int8"|"int16"|"float32"
- HABITHON_CT2_THREADS                  -> integer CPU threads
- HABITHON_MARIAN_DEVICE                -> "cpu"
- HABITHON_MARIAN_DTYPE                 -> "auto"|"float32"|...
- HABITHON_MARIAN_CACHE_DIR             -> HF cache directory path
- HABITHON_LANGID_MODEL_PATH            -> path to lid.176.bin
- HABITHON_MT_CACHE_PATH                -> cache root (sqlite file or fs dir)
- HABITHON_IO_OVERWRITE                 -> "1"/"true" to enable overwrite
- HABITHON_IO_DRY_RUN                   -> "1"/"true" to enable dry-run
- HABITHON_IO_WORKERS                   -> integer worker count
- HABITHON_LOG_LEVEL                    -> "DEBUG"|"INFO"|"WARN"

Precedence: environment -> defaults in this file.

Validation helper:
- validate_translation_settings(mapping) -> (ok: bool, issues: list[str])
- capabilities_summary(mapping) -> str

Note: This file avoids heavy imports and does not perform any model loading.
"""

from types import MappingProxyType
import os
from datetime import datetime, timezone


# ---------------------------
# Small utilities (import-light)
# ---------------------------

def _env(key, default=None):
    v = os.environ.get(key)
    return v if v is not None and v != "" else default


def _env_int(key, default):
    v = os.environ.get(key)
    if v is None or v == "":
        return default
    try:
        return int(v)
    except Exception:
        return default


def _env_bool(key, default):
    v = os.environ.get(key)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def _freeze(obj):
    """Recursively make mappings read-only and lists/sets tuples.

    - dict -> MappingProxyType of frozen children
    - list/tuple/set -> tuple of frozen children
    - otherwise unchanged
    """
    if isinstance(obj, dict):
        return MappingProxyType({k: _freeze(v) for k, v in obj.items()})
    if isinstance(obj, (list, tuple, set)):
        return tuple(_freeze(v) for v in obj)
    return obj


def _cpu_default_half(min_value=1):
    cores = os.cpu_count() or 2
    return max(min_value, (cores + 1) // 2)


# ---------------------------
# Defaults
# ---------------------------

# Repository-relative defaults (kept simple; validation will check existence)
_DEFAULT_CT2_MODEL_DIR = _env(
    "HABITHON_CT2_MODEL_DIR",
    "resources/models/ct2/nllb-200-distilled-600M",
)
_DEFAULT_LANGID_MODEL = _env(
    "HABITHON_LANGID_MODEL_PATH",
    "resources/models/fasttext/lid.176.bin",
)
_DEFAULT_MT_CACHE_PATH = _env(
    "HABITHON_MT_CACHE_PATH",
    "outputs/mt_cache/mt_cache.sqlite",
)

# Engine selection
_ENGINE = (_env("HABITHON_TRANSLATION_ENGINE", "ct2_nllb") or "ct2_nllb").strip()

# CT2 overrides
_CT2_DEVICE = (_env("HABITHON_CT2_DEVICE", "cpu") or "cpu").strip()
_CT2_COMPUTE_TYPE = (_env("HABITHON_CT2_COMPUTE_TYPE", "int8") or "int8").strip()
_CT2_THREADS = _env_int("HABITHON_CT2_THREADS", _cpu_default_half())

# Marian overrides
_MARIAN_DEVICE = (_env("HABITHON_MARIAN_DEVICE", "cpu") or "cpu").strip()
_MARIAN_DTYPE = (_env("HABITHON_MARIAN_DTYPE", "auto") or "auto").strip()
# Prefer a repo-local HF cache dir by default to support offline
_MARIAN_CACHE_DIR = _env("HABITHON_MARIAN_CACHE_DIR", "resources/models/hf")

# IO/log overrides
_IO_OVERWRITE = _env_bool("HABITHON_IO_OVERWRITE", False)
_IO_DRY_RUN = _env_bool("HABITHON_IO_DRY_RUN", False)
_IO_WORKERS = _env_int("HABITHON_IO_WORKERS", _cpu_default_half())
_LOG_LEVEL = (_env("HABITHON_LOG_LEVEL", "INFO") or "INFO").strip().upper()

# Common language lists/maps
# ISO-like -> NLLB tag
_NLLB_LANG_MAP = {
    "sk": "slk_Latn",
    "cs": "ces_Latn",
    "de": "deu_Latn",
    "pl": "pol_Latn",
    "hu": "hun_Latn",
    "en": "eng_Latn",
}

# Marian local model identifiers or paths (must be available offline via cache)
_MARIAN_MODELS = {
    # You may mirror these locally or ensure they exist in the HF cache dir
    "sk": "Helsinki-NLP/opus-mt-sk-en",
    "cs": "Helsinki-NLP/opus-mt-cs-en",
    "de": "Helsinki-NLP/opus-mt-de-en",
    "pl": "Helsinki-NLP/opus-mt-pl-en",
    "hu": "Helsinki-NLP/opus-mt-hu-en",
}


# ---------------------------
# Build the settings mapping (then deep-freeze)
# ---------------------------

_translation = {
    # 1) Engine selection
    "engine": _ENGINE,
    "tgt_lang": "en",
    "strict": True,

    # 2) Model locations (offline only)
    "ct2_nllb": {
        "model_dir": _DEFAULT_CT2_MODEL_DIR,
        "compute_type": _CT2_COMPUTE_TYPE,  # "int8"|"int16"|"float32"
        "device": _CT2_DEVICE,              # "cpu"|"cuda"
        "num_threads": _CT2_THREADS,
        "src_lang_map": _NLLB_LANG_MAP,
        "tgt_lang_code": "eng_Latn",
    },
    "marian": {
        "models": _MARIAN_MODELS,          # ISO-like -> model id/path
        "device": _MARIAN_DEVICE,
        "dtype": _MARIAN_DTYPE,            # "auto" or explicit
        "local_files_only": True,
        "hf_cache_dir": _MARIAN_CACHE_DIR,
    },

    # 3) Decoding & determinism
    "decoding": {
        "ct2": {
            "beam_size": 4,
            "length_penalty": 1.0,
            "max_batch_size": 8,
            "max_tokens": 256,
            "seed": 42,
        },
        "marian": {
            "num_beams": 4,
            "length_penalty": 1.0,
            "max_batch_size": 8,
            "max_new_tokens": 256,
            "no_repeat_ngram_size": 3,
            "seed": 42,
        },
    },

    # 4) Segmenter options (Markdown)
    "segmenter": {
        "segment_max_chars": 1200,
        "translate_alt_text": True,
        "translate_link_label": True,
        "translate_table_cells": True,
        "preserve_whitespace": True,
        "collapse_softbreaks": True,
        "language_hint": None,
    },

    # 5) Language detection (routing)
    "langid": {
        "impl": "fasttext",
        "model_path": _DEFAULT_LANGID_MODEL,
        "max_chars": 5000,
        "min_chars": 50,
        "candidates": ["sk", "de", "cs", "pl", "hu", "en"],
    },

    # 6) Glossary integration (optional)
    "glossary": {
        "enabled": False,
        "mode": "none",               # "pre"|"post"|"both"|"none"
        "glossary_id": None,
        "db_path": "resources/glossary/glossary.sqlite",
        "regex_enabled": False,
    },

    # 7) Cache integration (optional)
    "cache": {
        "enabled": True,
        "backend": "sqlite",          # "sqlite"|"fs"
        "root_path": _DEFAULT_MT_CACHE_PATH,
        "max_value_bytes": 1000000,
        "max_items": None,
        "default_ttl_seconds": None,
        "namespace": "ct2-nllb@eng_Latn",
    },

    # 8) Writer & I/O policy (as used by the app)
    "io": {
        "overwrite": _IO_OVERWRITE,
        "dry_run": _IO_DRY_RUN,
        "workers": _IO_WORKERS,
        "on_error": "skip",  # "skip"|"fail_fast"
    },

    # 9) Safety & policy flags
    "policy": {
        "network_access": False,
        "allow_online_model_download": False,
        "preserve_structure": True,
        "collect_telemetry": False,  # no content-level telemetry
    },

    # 10) Logging & telemetry (lightweight)
    "logging": {
        "level": _LOG_LEVEL,        # "INFO"|"DEBUG"|"WARN"
        "progress": "auto",         # "auto"|"plain"|"none"
        "redact_paths": True,
    },
    "telemetry": {
        "enabled": True,            # attach per-phase timings & counts
        "engine_fingerprint": True, # include model dir/hash in metadata
    },

    # 11) Schema/versioning
    "schema": {
        "version": 1,
        "last_updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    },
}

# Public, read-only mapping
TRANSLATION = _freeze(_translation)

__all__ = [
    "TRANSLATION",
    "validate_translation_settings",
    "capabilities_summary",
]


# ---------------------------
# Validation helper
# ---------------------------

def _path_exists(p):
    try:
        return os.path.exists(p)
    except Exception:
        return False


def _is_file_like(p):
    # Heuristic: treat as file if it looks like it has an extension
    base, ext = os.path.splitext(str(p))
    return bool(ext)


def validate_translation_settings(cfg=None):
    """
    Validate the translation settings. Lightweight, no heavy imports.

    Args:
        cfg: mapping to validate; defaults to this module's TRANSLATION

    Returns:
        (ok, issues): ok is True when no issues found; otherwise False and a list
        of human-readable strings explaining the issues.

    Checks performed (selected by active engine):
      - Required paths exist (ct2 model dir; langid model; cache parent; glossary if enabled)
      - Engine compatibility (required fields present)
      - Language coverage for selected engine
      - Determinism knobs sanity (no sampling flags; beams/penalties/ranges)
    """
    issues = []
    cfg = cfg or TRANSLATION

    # Paths
    langid_model = cfg["langid"]["model_path"]
    if not _path_exists(langid_model):
        issues.append("LangID model not found: {0}".format(langid_model))

    cache_root = cfg["cache"]["root_path"]
    cache_parent = os.path.dirname(cache_root) if _is_file_like(cache_root) else cache_root
    if not _path_exists(cache_parent):
        issues.append("Cache parent path does not exist: {0}".format(cache_parent))

    if bool(cfg["glossary"]["enabled"]):
        gl_path = cfg["glossary"]["db_path"]
        if not _path_exists(gl_path):
            issues.append("Glossary DB not found but glossary.enabled=True: {0}".format(gl_path))

    engine = str(cfg["engine"]).strip()
    strict = bool(cfg.get("strict", False))

    # Engine-specific checks
    if engine == "ct2_nllb":
        ct2 = cfg["ct2_nllb"]
        model_dir = ct2["model_dir"]
        if not _path_exists(model_dir):
            issues.append("CT2/NLLB model_dir not found: {0}".format(model_dir))
        if ct2["device"] not in {"cpu", "cuda"}:
            issues.append("CT2 device invalid: {0}".format(ct2['device']))
        if ct2["compute_type"] not in {"int8", "int16", "float32"}:
            issues.append("CT2 compute_type unusual: {0}".format(ct2['compute_type']))
        if int(ct2["num_threads"]) < 1:
            issues.append("CT2 num_threads must be >= 1")
        # Language coverage
        src_map = dict(ct2.get("src_lang_map", {}))
        expected_langs = cfg["langid"].get("candidates") or list(src_map.keys())
        for lang in expected_langs:
            if lang == "en":
                continue
            if lang not in src_map:
                issues.append("CT2 src_lang_map missing language: '{0}'".format(lang))
        # Decoding/determinism
        dec = cfg["decoding"]["ct2"]
        if int(dec["beam_size"]) < 1:
            issues.append("CT2 beam_size must be >= 1")
        if float(dec["length_penalty"]) < 0:
            issues.append("CT2 length_penalty must be >= 0")
        if int(dec["max_batch_size"]) < 1:
            issues.append("CT2 max_batch_size must be >= 1")
        if int(dec["max_tokens"]) < 1:
            issues.append("CT2 max_tokens must be >= 1")
    elif engine == "marian_opus":
        mar = cfg["marian"]
        models = dict(mar.get("models", {}))
        if not models:
            issues.append("Marian engine selected but no models configured")
        if mar["device"] != "cpu":
            issues.append("Marian device should be 'cpu' for offline policy (got {0})".format(mar['device']))
        if not bool(mar.get("local_files_only", True)):
            issues.append("Marian local_files_only should be True for offline policy")
        # Language coverage
        expected_langs = cfg["langid"].get("candidates") or list(models.keys())
        for lang in expected_langs:
            if lang == "en":
                continue
            if lang not in models:
                issues.append("Marian models missing language: '{0}'".format(lang))
        # Decoding/determinism
        dec = cfg["decoding"]["marian"]
        if int(dec["num_beams"]) < 1:
            issues.append("Marian num_beams must be >= 1")
        if float(dec["length_penalty"]) < 0:
            issues.append("Marian length_penalty must be >= 0")
        if int(dec["max_batch_size"]) < 1:
            issues.append("Marian max_batch_size must be >= 1")
        if int(dec["max_new_tokens"]) < 1:
            issues.append("Marian max_new_tokens must be >= 1")
    else:
        issues.append("Unknown engine: {0}".format(engine))

    # Policy constraints
    if bool(cfg["policy"]["network_access"]):
        issues.append("policy.network_access must be False for this project")
    if bool(cfg["policy"]["allow_online_model_download"]):
        issues.append("policy.allow_online_model_download must be False (offline only)")

    ok = len(issues) == 0
    if strict and not ok:
        # In strict mode, the caller might want to raise; we just return issues.
        pass

    return ok, issues


def capabilities_summary(cfg=None):
    """Return a compact capabilities string for logging.

    Example: "engine=ct2_nllb device=cpu compute=int8 threads=8 cache=on(sqlite) glossary=off"
    """
    cfg = cfg or TRANSLATION
    engine = str(cfg["engine"]).strip()

    if engine == "ct2_nllb":
        ct2 = cfg["ct2_nllb"]
        device = ct2["device"]
        compute = ct2["compute_type"]
        threads = ct2["num_threads"]
        engine_part = "engine=ct2_nllb device={0} compute={1} threads={2}".format(device, compute, threads)
    elif engine == "marian_opus":
        mar = cfg["marian"]
        device = mar["device"]
        dtype = mar["dtype"]
        engine_part = "engine=marian_opus device={0} dtype={1}".format(device, dtype)
    else:
        engine_part = "engine={0}".format(engine)

    cache = cfg["cache"]
    cache_part = "cache=on(" + str(cache["backend"]) + ")" if cache.get("enabled") else "cache=off"
    glossary = cfg["glossary"]
    gl_part = "glossary=on" if glossary.get("enabled") else "glossary=off"

    return "{0} {1} {2}".format(engine_part, cache_part, gl_part)
