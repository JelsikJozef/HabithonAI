from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.preprocessing.app.guardrails import anonymization_sanity
from src.shared.llm.openai_client import summarize_keywords, OpenAIClientError

# Stable policy descriptor for audit/versioning
SUMMARIZER_POLICY_VERSION = "step3-sumkw-v1"

# Output layout constants
ARTIFACTS_ROOT = Path("outputs/artifacts")
LOGS_ROOT = Path("outputs/logs")


@dataclass
class Step3Config:
    model: str = "gpt-5-mini"
    timeout_s: int = 60
    max_input_chars: int = 500_000
    prompt_template_version: str = SUMMARIZER_POLICY_VERSION


@dataclass
class Step3Inputs:
    document_uid: str
    content_hash: Optional[str] = None
    context: Dict[str, Any] | None = None
    config: Step3Config | None = None


# -----------------------------
# Logger
# -----------------------------


def _setup_logger(doc_uid: str, run_id: str | None) -> logging.Logger:
    LOGS_ROOT.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"step3.{doc_uid}")
    logger.setLevel(logging.INFO)
    if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        fname = f"step3_{doc_uid}_{run_id or 'norun'}.log"
        fh = logging.FileHandler(LOGS_ROOT / fname, encoding="utf-8")
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


# -----------------------------
# Helpers for Step 1 artifact loading and validation
# -----------------------------


def _load_step1(doc_uid: str) -> tuple[bool, dict[str, Any], str, str]:
    """Return (ok, step1_json, normalized_text, step1_dir).

    ok is False when inputs are missing or Step 1 failed.
    """
    step1_dir = ARTIFACTS_ROOT / doc_uid / "step1"
    result_path = step1_dir / "result.json"
    norm_path = step1_dir / "normalized.txt"
    if not result_path.exists() or not norm_path.exists():
        return False, {"errors": [{"code": "missing_input"}]}, "", str(step1_dir)
    try:
        step1 = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
        return False, {"errors": [{"code": "invalid_input_json"}]}, "", str(step1_dir)
    status = step1.get("status")
    if status != "ok":
        return False, {"errors": [{"code": "step1_failed"}]}, "", str(step1_dir)
    try:
        normalized_text = norm_path.read_text(encoding="utf-8")
    except Exception:
        return False, {"errors": [{"code": "missing_input"}]}, "", str(step1_dir)
    return True, step1, normalized_text, str(step1_dir)


def _canonical_meta_from_step1(step1: dict[str, Any]) -> dict[str, Any]:
    meta = step1.get("canonical_metadata") or {}
    return dict(meta)


def _compute_content_hash(normalized_text: str, canonical_meta: Dict[str, Any]) -> str:
    # Must mirror Step 1's hashing: text + "\n" + sorted-keys JSON of canonical meta
    meta_json = json.dumps(
        canonical_meta, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    h = hashlib.sha256()
    h.update(normalized_text.encode("utf-8"))
    h.update(b"\n")
    h.update(meta_json.encode("utf-8"))
    return h.hexdigest()


# -----------------------------
# LLM output validation
# -----------------------------


_ALLOWED_KW_RE = re.compile(r"^[a-z0-9\- ]+$")


def _validate_summary(summary: str) -> bool:
    if not isinstance(summary, str):
        return False
    s = summary.strip()
    if not s:
        return False
    # One sentence heuristic: must end with . ! or ? and contain that punctuation at most once
    if s[-1:] not in ".!?":
        return False
    if len(re.findall(r"[.!?]", s)) != 1:
        return False
    # Word count <= 30 (split on whitespace)
    words = [w for w in re.split(r"\s+", s) if w]
    if len(words) > 30:
        return False
    # No newlines
    if "\n" in s or "\r" in s:
        return False
    return True


def _validate_keywords(keywords: List[str]) -> bool:
    if not isinstance(keywords, list) or len(keywords) != 5:
        return False
    seen: set[str] = set()
    for kw in keywords:
        if not isinstance(kw, str):
            return False
        s = kw.strip()
        if not s:
            return False
        # lowercase only
        if s.lower() != s:
            return False
        if not _ALLOWED_KW_RE.match(s):
            return False
        if s in seen:
            return False
        seen.add(s)
    return True


# -----------------------------
# Persistence
# -----------------------------


def _persist_step3(
    doc_uid: str, output: dict[str, Any], *, summary: Optional[str], keywords: Optional[List[str]]
) -> None:
    step3_dir = ARTIFACTS_ROOT / doc_uid / "step3"
    step3_dir.mkdir(parents=True, exist_ok=True)

    # Always write result.json
    (step3_dir / "result.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if output.get("status") != "ok":
        return

    if summary is not None:
        (step3_dir / "summary.txt").write_text(summary, encoding="utf-8")
    if keywords is not None:
        (step3_dir / "keywords.json").write_text(
            json.dumps(keywords, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def write_merged_metadata(doc_uid: str, merged_meta: dict[str, Any]) -> None:
    """Write the per-document metadata sidecar ``outputs/artifacts/{doc_uid}/metadata_merged.json``.

    Public so callers that bind a single LLM-generated payload to multiple document variants
    (see :func:`bind_variant_metadata`) reuse the same on-disk schema/location.
    """
    out_path = ARTIFACTS_ROOT / doc_uid / "metadata_merged.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(merged_meta, ensure_ascii=False, indent=2), encoding="utf-8")


# Backwards-compatible private alias used within run_step3.
_persist_merged_metadata = write_merged_metadata


def bind_variant_metadata(
    *,
    english_uid: str,
    original_uid: Optional[str],
    english_meta: dict[str, Any],
    original_meta: Optional[dict[str, Any]],
    summary: str,
    keywords: List[str],
) -> dict[str, Any]:
    """Attach one LLM-generated metadata payload to both document variants.

    The summary/keywords are generated once over the anonymized English variant and bound to
    both the original and English ``doc_uid`` with explicit cross-variant links, so the
    representations stay connected as one logical document (návrh 2.3.6 / 2.3.7). Each variant
    keeps its OWN canonical metadata (e.g. the original may carry ``language: sk``) but shares
    the same payload and link block. When ``original_uid`` is ``None`` (already-English source)
    only the English sidecar is written.

    Returns ``{"english": <merged>, "original": <merged>|None}``.
    """
    payload = {
        "summary_one_sentence": summary,
        "keywords_top5": keywords,
        "tool_versions": {"summarizer_policy_version": SUMMARIZER_POLICY_VERSION},
    }
    variants: dict[str, str] = {"en": english_uid}
    if original_uid is not None:
        variants["orig"] = original_uid
    links = {
        "variants": dict(variants),
        "metadata_source": {"variant": "en", "document_uid": english_uid},
    }

    en_merged = {
        **english_meta,
        **payload,
        "variant": "en",
        "document_uid": english_uid,
        **links,
    }
    write_merged_metadata(english_uid, en_merged)

    orig_merged: Optional[dict[str, Any]] = None
    if original_uid is not None:
        orig_merged = {
            **(original_meta or {}),
            **payload,
            "variant": "orig",
            "document_uid": original_uid,
            **links,
        }
        write_merged_metadata(original_uid, orig_merged)

    return {"english": en_merged, "original": orig_merged}


# -----------------------------
# Public API
# -----------------------------


def run_step3(inputs: Step3Inputs) -> Dict[str, Any]:
    t0 = time.time()
    cfg = inputs.config or Step3Config()
    # Env overrides
    model_env = os.environ.get("SUMMARIZER_MODEL")
    timeout_env = os.environ.get("SUMMARIZER_TIMEOUT_S")
    if isinstance(model_env, str) and model_env.strip():
        cfg.model = model_env.strip()
    if isinstance(timeout_env, str) and timeout_env.strip().isdigit():
        try:
            cfg.timeout_s = int(timeout_env.strip())
        except Exception:
            pass

    doc_uid = inputs.document_uid
    run_id = ""
    if isinstance(inputs.context, dict):
        run_id = str(inputs.context.get("run_id", ""))

    logger = _setup_logger(doc_uid, run_id or None)

    ok, step1, normalized_text, _ = _load_step1(doc_uid)
    if not ok:
        result = {
            "status": "failed",
            "errors": step1.get("errors", [{"code": "missing_input"}]),
        }
        _persist_step3(doc_uid, result, summary=None, keywords=None)
        return result

    # Guardrails: anonymization sanity FIRST to avoid leaking content or depending on hash
    checks = {
        "hash_match": False,
        "summary_shape": False,
        "keywords_shape": False,
        "anonymization_sanity": False,
    }
    anon_ok, anon_info = anonymization_sanity(normalized_text)
    checks["anonymization_sanity"] = anon_ok
    if not anon_ok:
        result = {
            "status": "failed",
            "errors": [{"code": "anonymization_failed"}],
            "checks": checks,
        }
        _persist_step3(doc_uid, result, summary=None, keywords=None)
        return result

    # Validate hash and uid
    canonical_meta = _canonical_meta_from_step1(step1)
    recomputed_hash = _compute_content_hash(normalized_text, canonical_meta)
    step1_hash = step1.get("content_hash")
    checks["hash_match"] = bool(
        step1_hash == recomputed_hash and (inputs.content_hash in (None, step1_hash))
    )

    if inputs.content_hash and inputs.content_hash != step1_hash:
        result = {"status": "failed", "errors": [{"code": "hash_mismatch"}], "checks": checks}
        _persist_step3(doc_uid, result, summary=None, keywords=None)
        return result
    if step1_hash != recomputed_hash:
        result = {"status": "failed", "errors": [{"code": "hash_mismatch"}], "checks": checks}
        _persist_step3(doc_uid, result, summary=None, keywords=None)
        return result

    # Size guard
    if len(normalized_text) > cfg.max_input_chars:
        result = {
            "status": "failed",
            "errors": [{"code": "too_large"}],
            "checks": checks,
        }
        _persist_step3(doc_uid, result, summary=None, keywords=None)
        return result

    # Call LLM deterministically
    t_call0 = time.time()
    usage: dict[str, Any] = {}
    warnings: List[str] = []
    try:
        llm_out = summarize_keywords(
            normalized_text,
            model=cfg.model,
            timeout_s=cfg.timeout_s,
            seed=0,
        )
    except OpenAIClientError as e:
        result = {
            "status": "failed",
            "errors": [{"code": "llm_call_failed", "message": str(e)}],
            "checks": checks,
        }
        _persist_step3(doc_uid, result, summary=None, keywords=None)
        return result
    t_call1 = time.time()

    # Note on GPT-5 models: sampling params omitted by client per model constraints
    try:
        if isinstance(cfg.model, str) and cfg.model.strip().lower().startswith("gpt-5"):
            warnings.append("gpt5_sampling_omitted")
    except Exception:
        pass

    summary = str(llm_out.get("summary", "")).strip()
    keywords = llm_out.get("keywords", []) or []
    if not isinstance(keywords, list):
        keywords = []
    keywords = [str(k) for k in keywords]
    usage = llm_out.get("usage", {}) or {}

    # Validate response shape
    sum_ok = _validate_summary(summary)
    kw_ok = _validate_keywords(keywords)
    checks["summary_shape"] = sum_ok
    checks["keywords_shape"] = kw_ok

    errors: List[dict[str, str]] = []
    if not sum_ok:
        errors.append({"code": "invalid_summary_shape"})
    if not kw_ok:
        errors.append({"code": "invalid_keywords_shape"})

    # Prepare outputs
    processing_report = {
        "run_id": run_id,
        "timings_ms": {
            "summarize": int((t_call1 - t_call0) * 1000),
            "total": int((time.time() - t0) * 1000),
        },
        "tokens": {
            "prompt": getattr(usage, "prompt_tokens", None)
            if hasattr(usage, "prompt_tokens")
            else usage.get("prompt_tokens"),
            "completion": getattr(usage, "completion_tokens", None)
            if hasattr(usage, "completion_tokens")
            else usage.get("completion_tokens"),
            "total": getattr(usage, "total_tokens", None)
            if hasattr(usage, "total_tokens")
            else usage.get("total_tokens"),
        },
        "warnings": warnings,
    }

    output: Dict[str, Any] = {
        "document_uid": doc_uid,
        "content_hash": step1_hash,
        "summary_one_sentence": summary if sum_ok and kw_ok else None,
        "keywords_top5": keywords if sum_ok and kw_ok else None,
        "summarizer_model": cfg.model,
        "summarizer_model_version": None,  # not provided by client currently
        "prompt_template_version": cfg.prompt_template_version,
        "processing_report": processing_report,
        "checks": checks,
        "status": "ok" if not errors else "failed",
    }
    if errors:
        output["errors"] = errors

    # Persist artifacts (only content files on success)
    _persist_step3(
        doc_uid,
        output,
        summary=summary if not errors else None,
        keywords=keywords if not errors else None,
    )

    # Merged metadata sidecar on success
    if not errors:
        merged_meta = dict(canonical_meta)
        merged_meta["summary_one_sentence"] = summary
        merged_meta["keywords_top5"] = keywords
        merged_meta["tool_versions"] = {
            "summarizer_policy_version": SUMMARIZER_POLICY_VERSION,
        }
        _persist_merged_metadata(doc_uid, merged_meta)

    # Structured log line (no content)
    logger.info(
        "step3 %s: uid=%s chars=%d model=%s hash_prefix=%s tokens_total=%s",
        output["status"],
        doc_uid,
        len(normalized_text),
        cfg.model,
        str(step1_hash)[:8] if isinstance(step1_hash, str) else "",
        processing_report["tokens"].get("total"),
    )

    return output
