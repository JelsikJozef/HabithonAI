from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Tuple

from src.preprocessing.adapters.encoding.utf8_normalizer import (
    NormalizerOptions,
    normalize_text,
)

# Stable policy descriptor for audit/versioning
NORMALIZATION_POLICY_VERSION = "step1-nfc-lf-v1"

# Output layout constants
ARTIFACTS_ROOT = Path("outputs/artifacts")
LOGS_ROOT = Path("outputs/logs")

# Max size guard for normalized text in bytes (UTF-8)
MAX_NORMALIZED_BYTES = 50 * 1024 * 1024  # 50 MB default ceiling


@dataclass
class Step1Inputs:
    text: str
    meta: Dict[str, Any]
    context: Dict[str, Any]
    # Document variant ("orig" vs "en"). It is the SOLE disambiguator that flows into
    # the content hash so distinct variants of the same text never collide on doc_uid.
    # Default "orig" keeps original-document identifiers unchanged.
    variant: str = "orig"


# -----------------------------
# Utility helpers
# -----------------------------


def _normalize_for_policy(text: str) -> Tuple[str, Dict[str, Any]]:
    """Apply the exact normalization policy required by Step 1.

    Rules:
    - UTF-8, strip BOM (handled by normalizer)
    - Unicode NFC only
    - Convert EOL to LF ("\n")
    - Preserve tabs as-is
    - Preserve all visual spaces including trailing spaces
    - Do not collapse blank lines
    - Do not add a final newline implicitly
    - Remove non-printing control characters except TAB
    - Do not reflow or wrap lines
    """
    opts = NormalizerOptions(
        unicode_form="NFC",
        normalize_nbsp="keep",
        eol_policy="lf",
        strip_control_chars=True,
        tabs_policy="keep",
        tab_width=4,  # unused when tabs_policy="keep"
        trim_trailing_spaces="none",
        collapse_blank_lines_to=None,
        ensure_final_newline=False,
        guard_code_fences=True,
        guard_inline_code=True,
        guard_tables=True,
        max_text_mb=50,
    )
    return normalize_text(text, opts)


def _normalize_variant(variant: str | None) -> str:
    """Map domain variant labels to stable hash tokens.

    "original" -> "orig", "english" -> "en"; recognized short tokens pass through.
    Unknown/empty values default to "orig".
    """
    v = (variant or "orig").strip().lower()
    mapping = {"original": "orig", "english": "en"}
    return mapping.get(v, v) or "orig"


def _canonicalize_meta(meta: Dict[str, Any], variant: str = "orig") -> Dict[str, Any]:
    """Select and sort stable metadata fields.

    Kept keys: doc_type, category, language, anonymizer_versions, source_path (optional).
    Excludes volatile fields like timestamps.

    The variant is included ONLY when it is not the original ("orig"), so that original
    documents retain their existing doc_uid while non-original variants (e.g. an English
    copy of an already-English document) hash to a distinct doc_uid. This is the single
    disambiguator; do not also encode the variant into chunk_id or artifact paths.
    """
    keys = ["doc_type", "category", "language", "anonymizer_versions"]
    out: Dict[str, Any] = {k: meta[k] for k in keys if k in meta}
    if "source_path" in meta:
        out["source_path"] = meta["source_path"]
    v = _normalize_variant(variant)
    if v != "orig":
        out["variant"] = v
    # Sort keys by using JSON dumps with sort_keys=True later
    return out


def _compute_content_hash(normalized_text: str, canonical_meta: Dict[str, Any]) -> str:
    # Serialize canonical meta deterministically: sorted keys, no spaces
    meta_json = json.dumps(
        canonical_meta, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    h = hashlib.sha256()
    h.update(normalized_text.encode("utf-8"))
    h.update(b"\n")
    h.update(meta_json.encode("utf-8"))
    return h.hexdigest()


def _derive_document_uid(content_hash_hex: str) -> str:
    # Take first 20 bytes of SHA-256 digest and base32-encode, lowercased without padding
    digest_bytes = bytes.fromhex(content_hash_hex)
    first20 = digest_bytes[:20]
    b32 = base64.b32encode(first20).decode("ascii").lower().rstrip("=")
    return f"doc_{b32}"


def _setup_logger(doc_uid: str, run_id: str | None) -> logging.Logger:
    LOGS_ROOT.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"step1.{doc_uid}")
    logger.setLevel(logging.INFO)
    # Avoid duplicate handlers if called repeatedly in tests
    if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        fname = f"step1_{doc_uid}_{run_id or 'norun'}.log"
        fh = logging.FileHandler(LOGS_ROOT / fname, encoding="utf-8")
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


# -----------------------------
# Validation and checks
# -----------------------------


def _validate_meta(meta: Dict[str, Any]) -> tuple[bool, list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    required = ["doc_type", "category", "language", "anonymizer_versions"]
    for k in required:
        if k not in meta or (isinstance(meta[k], str) and meta[k].strip() == ""):
            errors.append({"code": "missing_meta", "field": k})
    # language must be en
    lang = meta.get("language")
    if lang is not None and str(lang).lower() != "en":
        errors.append({"code": "invalid_language", "field": "language"})
    return len(errors) == 0, errors


# Reasonably obvious PII patterns
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
_SSN_US_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def _anonymization_sanity(text: str) -> tuple[bool, dict[str, Any]]:
    matches = {
        "email": bool(_EMAIL_RE.search(text)),
        "phone": bool(_PHONE_RE.search(text)),
        "ssn_us": bool(_SSN_US_RE.search(text)),
    }
    placeholders_present = bool(re.search(r"\[(?:PERSON|EMAIL|PHONE|ADDRESS|ORG)]", text))
    passed = not any(matches.values())
    info = {"patterns": matches, "placeholders_present": placeholders_present}
    return passed, info


def _determinism_check(
    text: str, canonical_meta: Dict[str, Any], content_hash_hex: str, doc_uid: str
) -> bool:
    # Recompute a second time and compare
    norm2, _ = _normalize_for_policy(text)
    if norm2 != text:
        # We must compare against the normalized input (caller provides normalized text)
        pass
    content_hash2 = _compute_content_hash(norm2, canonical_meta)
    doc_uid2 = _derive_document_uid(content_hash2)
    return content_hash2 == content_hash_hex and doc_uid2 == doc_uid


# -----------------------------
# Main API
# -----------------------------


def run_step1(inputs: Step1Inputs) -> Dict[str, Any]:
    """Run ingestion Step 1 and persist artifacts.

    Returns the output contract dict; also writes artifacts/logs.
    """
    t0 = time.time()

    # Normalize text strict per policy
    normalized_text, norm_report = _normalize_for_policy(inputs.text)

    # Validate sizes and non-empty
    size_bytes = len(normalized_text.encode("utf-8"))
    if size_bytes == 0:
        status = {
            "status": "failed",
            "errors": [{"code": "empty_text", "message": "Normalized text is empty"}],
        }
        return status
    if size_bytes > MAX_NORMALIZED_BYTES:
        status = {
            "status": "failed",
            "errors": [
                {
                    "code": "text_too_large",
                    "message": f"Normalized text size {size_bytes} exceeds limit {MAX_NORMALIZED_BYTES}",
                }
            ],
        }
        return status

    # Canonical metadata and validation
    canonical_meta = _canonicalize_meta(inputs.meta or {}, inputs.variant)
    meta_ok, meta_errors = _validate_meta(canonical_meta)

    # Compute content hash and document UID regardless, to have a stable reference
    content_hash = _compute_content_hash(normalized_text, canonical_meta)
    document_uid = _derive_document_uid(content_hash)

    # Logger setup
    run_id = str(inputs.context.get("run_id", "")) if isinstance(inputs.context, dict) else ""
    logger = _setup_logger(document_uid, run_id or None)

    # Anonymization sanity
    anon_ok, anon_info = _anonymization_sanity(normalized_text)

    # Determinism check (should always pass)
    deterministic = _determinism_check(normalized_text, canonical_meta, content_hash, document_uid)

    # Compile checks structure
    checks = {
        "meta_validation": {"passed": meta_ok, "errors": meta_errors},
        "anonymization_sanity": {
            "passed": anon_ok,
            "patterns": anon_info.get("patterns", {}),
            "placeholders_present": anon_info.get("placeholders_present", False),
        },
        "determinism_check": {"passed": deterministic},
    }

    t1 = time.time()

    # Processing report (avoid logging secrets or full content)
    processing_report = {
        "run_id": run_id,
        "timings_ms": {
            "normalize": int((t1 - t0) * 1000),
        },
        "warnings": norm_report.get("warnings", []),
        "fixes": {
            "bom_removed": bool(norm_report.get("bom_removed")),
            "control_chars_removed": int(norm_report.get("control_chars_removed", 0)),
        },
        "input_bytes": len(inputs.text.encode("utf-8")) if isinstance(inputs.text, str) else None,
        "output_bytes": size_bytes,
    }

    # Prepare output object
    output: Dict[str, Any] = {
        "document_uid": document_uid,
        "content_hash": content_hash,
        "normalized_text": normalized_text,
        "canonical_metadata": canonical_meta,
        "normalization_policy_version": NORMALIZATION_POLICY_VERSION,
        "processing_report": processing_report,
        "checks": checks,
        "status": "ok" if (meta_ok and anon_ok and deterministic) else "failed",
    }

    # Persist artifacts
    artifacts_dir = ARTIFACTS_ROOT / document_uid / "step1"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # Always write JSON output for auditability
    result_json_path = artifacts_dir / "result.json"
    with open(result_json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, sort_keys=False)

    # If any check failed, do not emit normalized.txt
    if output["status"] != "ok":
        logger.info(
            "step1 failed: uid=%s hash=%s meta_ok=%s anon_ok=%s deterministic=%s size=%dB",
            document_uid,
            content_hash,
            meta_ok,
            anon_ok,
            deterministic,
            size_bytes,
        )
        return output

    # Write normalized text and sidecar summary
    normalized_txt_path = artifacts_dir / "normalized.txt"
    with open(normalized_txt_path, "w", encoding="utf-8", newline="") as f:
        # Write exact normalized text bytes (newline="" prevents universal newline conversion)
        f.write(normalized_text)

    summary_path = artifacts_dir / "summary.txt"
    summary_lines = [
        f"document_uid: {document_uid}",
        f"content_hash: {content_hash}",
        f"policy: {NORMALIZATION_POLICY_VERSION}",
        f"size_bytes: {size_bytes}",
        f"doc_type: {canonical_meta.get('doc_type')}",
        f"category: {canonical_meta.get('category')}",
        f"language: {canonical_meta.get('language')}",
        f"source_path: {canonical_meta.get('source_path','')}",
        f"anonymization_ok: {anon_ok}",
        f"control_chars_removed: {norm_report.get('control_chars_removed', 0)}",
    ]
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(summary_lines) + "\n")

    # Structured log line (no content)
    logger.info(
        "step1 ok: uid=%s hash=%s size=%dB meta_keys=%s warnings=%d",
        document_uid,
        content_hash,
        size_bytes,
        sorted(list(canonical_meta.keys())),
        len(norm_report.get("warnings", [])),
    )

    return output


# -----------------------------
# Tiny CLI for local runs
# -----------------------------


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    import argparse

    parser = argparse.ArgumentParser(description="Step 1: Input normalization and hashing")
    parser.add_argument("--in", dest="infile", required=True, help="Path to input JSON file")
    parser.add_argument(
        "--out-uid", dest="print_uid", action="store_true", help="Print the document_uid to stdout"
    )
    args = parser.parse_args(argv)

    # Read input JSON
    with open(args.infile, "r", encoding="utf-8") as f:
        payload = json.load(f)

    text = payload.get("text", "")
    meta = payload.get("meta", {})
    context = payload.get("context", {})
    variant = payload.get("variant", "orig")

    result = run_step1(Step1Inputs(text=text, meta=meta, context=context, variant=variant))

    if args.print_uid and isinstance(result, dict):
        print(result.get("document_uid", ""))

    # Exit non-zero on failure
    return 0 if result.get("status") == "ok" else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
