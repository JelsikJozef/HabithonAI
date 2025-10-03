from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Stable policy descriptor for audit/versioning
CHUNKING_POLICY_VERSION = "step2-struct-v1"

# Output layout constants
ARTIFACTS_ROOT = Path("outputs/artifacts")
LOGS_ROOT = Path("outputs/logs")


@dataclass
class Step2Config:
    target_chunk_chars: int = 1_500
    hard_max_chunk_chars: int = 2_500
    min_chunk_chars: int = 400
    overlap_chars: int = 200


@dataclass
class Step2Inputs:
    document_uid: str
    content_hash: Optional[str] = None
    context: Dict[str, Any] | None = None
    config: Step2Config | None = None


# -----------------------------
# Logger
# -----------------------------


def _setup_logger(doc_uid: str, run_id: str | None) -> logging.Logger:
    LOGS_ROOT.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"step2.{doc_uid}")
    logger.setLevel(logging.INFO)
    if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        fname = f"step2_{doc_uid}_{run_id or 'norun'}.log"
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
    # Keep as-is (already canonicalized in step 1)
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
# Block model and parsing
# -----------------------------


@dataclass
class Block:
    kind: str  # heading|paragraph|list|table|code|blockquote|blank|other
    start: int  # offset in text
    end: int  # offset in text (exclusive)
    text: str
    level: int = 0  # heading level or list nesting hint
    title: str | None = None  # for headings


_ATX_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})[ \t]+(.+?)\s*#*\s*$")
_SETEXT_UNDER_RE = re.compile(r"^\s{0,3}(=+|-+)\s*$")
_LIST_BULLET_RE = re.compile(r"^\s{0,3}([*+-])\s+.+$")
_LIST_ORDERED_RE = re.compile(r"^\s{0,3}(\d{1,9})\.\s+.+$")
_BLOCKQUOTE_RE = re.compile(r"^\s{0,3}>\s?.*$")
_CODE_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_TABLE_LIKE_RE = re.compile(r"\|")
_TABLE_HEADER_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")


def _iter_lines_with_spans(text: str) -> List[tuple[int, int, str]]:
    lines: List[tuple[int, int, str]] = []
    i = 0
    n = len(text)
    while i < n:
        j = text.find("\n", i)
        if j == -1:
            lines.append((i, n, text[i:n]))
            break
        else:
            lines.append((i, j + 1, text[i : j + 1]))
            i = j + 1
    if n == 0:
        lines.append((0, 0, ""))
    return lines


def _parse_blocks(text: str) -> List[Block]:
    blocks: List[Block] = []
    lines = _iter_lines_with_spans(text)
    i = 0
    in_code = False
    fence_marker: str | None = None
    code_start = 0  # initialize for static analyzers
    while i < len(lines):
        ls, le, s = lines[i]
        # Code fence close detection takes priority when in_code
        if in_code:
            if _CODE_FENCE_RE.match(s):
                # Must match the same fence type (backticks or tildes)
                m = _CODE_FENCE_RE.match(s)
                assert m
                cur = m.group(1)
                if fence_marker and cur[0] == fence_marker[0]:
                    # include this line and finish
                    end = le
                    blocks.append(
                        Block(kind="code", start=code_start, end=end, text=text[code_start:end])
                    )
                    in_code = False
                    fence_marker = None
                    i += 1
                    continue
            # still in code
            i += 1
            continue

        # Blank line
        if s.strip() == "":
            blocks.append(Block(kind="blank", start=ls, end=le, text=s))
            i += 1
            continue

        # Code fence start
        m_code = _CODE_FENCE_RE.match(s)
        if m_code:
            in_code = True
            fence_marker = m_code.group(1)
            code_start = ls
            i += 1
            # Consume until closing fence (handled at loop top)
            while i < len(lines):
                ls2, le2, s2 = lines[i]
                if _CODE_FENCE_RE.match(s2):
                    m2 = _CODE_FENCE_RE.match(s2)
                    assert m2
                    if m2.group(1)[0] == fence_marker[0]:
                        # include closing and emit at top in next iteration
                        break
                i += 1
            continue

        # ATX heading
        m_h = _ATX_HEADING_RE.match(s.rstrip("\n"))
        if m_h:
            level = len(m_h.group(1))
            title = m_h.group(2).strip()
            blocks.append(Block(kind="heading", start=ls, end=le, text=s, level=level, title=title))
            i += 1
            continue

        # Setext heading (need lookahead)
        if i + 1 < len(lines):
            next_line = lines[i + 1][2]
            if _SETEXT_UNDER_RE.match(next_line.rstrip("\n")) and s.strip() != "":
                underline = _SETEXT_UNDER_RE.match(next_line.rstrip("\n"))
                assert underline
                level = 1 if underline.group(1).startswith("=") else 2
                end = lines[i + 1][1]
                blocks.append(
                    Block(
                        kind="heading",
                        start=ls,
                        end=end,
                        text=text[ls:end],
                        level=level,
                        title=s.rstrip("\n").strip(),
                    )
                )
                i += 2
                continue

        # Table-like detection: if current line and at least next non-blank contains '|'
        if _TABLE_LIKE_RE.search(s) and s.strip() != "":
            # Look ahead a few lines to confirm a table header separator or multiple '|' lines
            j = i + 1
            saw_pipe_lines = 1
            saw_sep = False
            while j < len(lines) and j < i + 6:
                s2 = lines[j][2]
                if s2.strip() == "":
                    break
                if _TABLE_LIKE_RE.search(s2):
                    saw_pipe_lines += 1
                    if _TABLE_HEADER_SEP_RE.match(s2.rstrip("\n")):
                        saw_sep = True
                        break
                else:
                    break
                j += 1
            if saw_pipe_lines >= 2 or saw_sep:
                # consume contiguous non-blank lines with pipes
                k = i
                end_k = lines[i][1]
                while k < len(lines):
                    s3 = lines[k][2]
                    if s3.strip() == "" or not _TABLE_LIKE_RE.search(s3):
                        break
                    end_k = lines[k][1]
                    k += 1
                blocks.append(Block(kind="table", start=ls, end=end_k, text=text[ls:end_k]))
                i = k
                continue

        # Blockquote
        if _BLOCKQUOTE_RE.match(s):
            k = i + 1
            end_k = le
            while k < len(lines) and _BLOCKQUOTE_RE.match(lines[k][2]):
                end_k = lines[k][1]
                k += 1
            blocks.append(Block(kind="blockquote", start=ls, end=end_k, text=text[ls:end_k]))
            i = k
            continue

        # List (bullet or ordered) - take contiguous list-like or indented continuation lines
        if _LIST_BULLET_RE.match(s) or _LIST_ORDERED_RE.match(s):
            k = i + 1
            end_k = le
            while k < len(lines):
                s2 = lines[k][2]
                if s2.strip() == "":
                    # include single blank line within list as continuation
                    if k + 1 < len(lines) and (
                        _LIST_BULLET_RE.match(lines[k + 1][2])
                        or _LIST_ORDERED_RE.match(lines[k + 1][2])
                    ):
                        end_k = lines[k][1]
                        k += 1
                        continue
                    break
                if (
                    _LIST_BULLET_RE.match(s2)
                    or _LIST_ORDERED_RE.match(s2)
                    or s2.startswith(" ")
                    or s2.startswith("\t")
                ):
                    end_k = lines[k][1]
                    k += 1
                    continue
                break
            blocks.append(Block(kind="list", start=ls, end=end_k, text=text[ls:end_k]))
            i = k
            continue

        # Paragraph: consume until blank line or next structural block start
        k = i + 1
        end_k = le
        while k < len(lines):
            s2 = lines[k][2]
            if s2.strip() == "":
                break
            # stop before next block starts
            if (
                _ATX_HEADING_RE.match(s2.rstrip("\n"))
                or _CODE_FENCE_RE.match(s2)
                or _BLOCKQUOTE_RE.match(s2)
                or _LIST_BULLET_RE.match(s2)
                or _LIST_ORDERED_RE.match(s2)
            ):
                break
            # conservative: if table-like next, stop
            if _TABLE_LIKE_RE.search(s2):
                # leave to table detector in its iteration
                break
            end_k = lines[k][1]
            k += 1
        blocks.append(Block(kind="paragraph", start=ls, end=end_k, text=text[ls:end_k]))
        i = k

    # Filter out leading/trailing blank blocks only when they are at boundaries; keep internal blanks
    # (We keep all blocks as parsed; chunker will include blanks as part of chunks when encountered.)
    return blocks


# -----------------------------
# Chunk assembly
# -----------------------------


def _sentence_split_pos(s: str, hard_limit: int, search_back: int = 300) -> int:
    """Find a split position <= hard_limit, preferring sentence boundary [.!?] then whitespace.
    Returns 0 if not found.
    """
    if hard_limit >= len(s):
        return len(s)
    lo = max(0, hard_limit - search_back)
    slice_ = s[lo:hard_limit]
    # Prefer punctuation followed by space or EOL
    m = re.search(r"[.!?](?=\s|$)", slice_)
    if m:
        return lo + m.end()
    # Fallback: last whitespace
    m2 = re.search(r"\s+(?!.*\s)", slice_)
    if m2:
        return lo + m2.end()
    return 0


def _assemble_chunks(
    text: str,
    blocks: List[Block],
    cfg: Step2Config,
) -> tuple[List[dict[str, Any]], List[str]]:
    warnings: List[str] = []

    # Work on a local copy to avoid mutating caller's blocks (for determinism)
    work_blocks: List[Block] = [
        Block(b.kind, b.start, b.end, b.text, b.level, b.title) for b in blocks
    ]

    # Validate atomic blocks size
    for b in work_blocks:
        if b.kind in {"code", "table"} and (b.end - b.start) > cfg.hard_max_chunk_chars:
            warnings.append(f"atomic_block_too_large:{b.kind}:{b.start}-{b.end}:{b.end - b.start}")
            # We fail later during QA

    chunks_raw: List[tuple[int, int, List[str]]] = []  # (start, end) coverage (unique), block kinds
    i = 0
    n = len(work_blocks)

    def can_add(cur_start: int, cur_end: int, add_block: Block, is_first_chunk: bool) -> bool:
        unique_len = (cur_end - cur_start) + (add_block.end - add_block.start)
        budget = (
            cfg.hard_max_chunk_chars
            if is_first_chunk
            else max(0, cfg.hard_max_chunk_chars - cfg.overlap_chars)
        )
        return unique_len <= budget

    while i < n:
        # Skip leading blanks in a new chunk only if they would exceed budget; otherwise include them
        is_first_chunk = len(chunks_raw) == 0
        cur_start = work_blocks[i].start
        cur_end = work_blocks[i].start
        kinds: List[str] = []

        # Greedily add blocks
        j = i
        while j < n:
            b = work_blocks[j]
            # Start from first non-blank as anchor for start offset
            if cur_end == cur_start and b.kind == "blank":
                # include a leading blank if room exists
                if not can_add(cur_start, cur_end, b, is_first_chunk):
                    break
                cur_end = b.end
                kinds.append(b.kind)
                j += 1
                continue

            if not can_add(cur_start, cur_end, b, is_first_chunk):
                # Attempt fallback split for non-atomic blocks when empty current (so block alone too big) or to reach target
                if b.kind not in {"code", "table"}:
                    available = (
                        cfg.hard_max_chunk_chars
                        if is_first_chunk
                        else max(0, cfg.hard_max_chunk_chars - cfg.overlap_chars)
                    ) - (cur_end - cur_start)
                    if available <= 0:
                        break
                    rel = _sentence_split_pos(b.text, available)
                    if rel > 0:
                        # create partial block
                        part_end = b.start + rel
                        cur_end = part_end
                        kinds.append(b.kind)
                        # adjust current block to start from part_end for next iteration
                        work_blocks[j] = Block(
                            kind=b.kind,
                            start=part_end,
                            end=b.end,
                            text=text[part_end : b.end],
                            level=b.level,
                            title=b.title,
                        )
                        break
                break

            # Add whole block
            cur_end = b.end
            kinds.append(b.kind)

            # Stop if reached target
            if (cur_end - cur_start) >= cfg.target_chunk_chars:
                j += 1
                break
            j += 1

        # Guard against empty chunk
        if cur_end <= cur_start:
            # Put at least one character to avoid deadlock
            next_end = min(len(text), cur_start + max(1, cfg.min_chunk_chars))
            chunks_raw.append((cur_start, next_end, kinds or ["other"]))
            i = j if j > i else i + 1
            continue

        chunks_raw.append((cur_start, cur_end, kinds))
        i = j if j > i else i + 1

    # Post-process to ensure min_chunk_chars by merging forward when needed (except last)
    merged: List[tuple[int, int, List[str]]] = []
    for idx, (s, e, klist) in enumerate(chunks_raw):
        if not merged:
            merged.append((s, e, klist))
            continue
        if (e - s) < cfg.min_chunk_chars:
            # try to merge into previous if combined size with overlap budget allows
            ps, pe, pk = merged[-1]
            # When merging, we just extend previous chunk unique coverage; kinds join
            merged[-1] = (ps, e, list(dict.fromkeys(pk + klist)))
        else:
            merged.append((s, e, klist))

    # Build final chunks texts with overlap
    chunks: List[dict[str, Any]] = []
    for ci, (s, e, klist) in enumerate(merged):
        overlap_from_prev = 0
        prefix = ""
        if ci > 0:
            prev_s, prev_e, _ = merged[ci - 1]
            overlap_from_prev = min(cfg.overlap_chars, prev_e - prev_s)
            if overlap_from_prev > 0:
                prefix = text[prev_e - overlap_from_prev : prev_e]
        chunk_text = prefix + text[s:e]
        chunks.append(
            {
                "offset_start": s,
                "offset_end": e,
                "text": chunk_text,
                "overlap_from_prev": overlap_from_prev,
                "block_kinds": list(dict.fromkeys(klist)),
            }
        )

    # Enforce hard_max after overlap; if any exceed, try to trim overlap first then fail
    for ch in chunks:
        if len(ch["text"]) > cfg.hard_max_chunk_chars:
            # trim overlap if possible
            excess = len(ch["text"]) - cfg.hard_max_chunk_chars
            if ch["overlap_from_prev"] > 0:
                trim = min(excess, ch["overlap_from_prev"])
                ch["text"] = ch["text"][trim:]
                ch["overlap_from_prev"] -= trim
            if len(ch["text"]) > cfg.hard_max_chunk_chars:
                warnings.append(
                    f"chunk_exceeds_hard_max:{ch['offset_start']}-{ch['offset_end']}:{len(ch['text'])}"
                )

    # Ensure full coverage: if the last chunk does not reach end, append tail chunks
    covered_end = chunks[-1]["offset_end"] if chunks else 0
    total_len = len(text)
    if covered_end < total_len:
        # Avoid splitting inside atomic blocks in tail; if tail overlaps any atomic block, warn (rare)
        intersects_atomic = any(
            (b.kind in {"code", "table"}) and not (b.end <= covered_end or b.start >= total_len)
            for b in work_blocks
        )
        if intersects_atomic:
            warnings.append(f"tail_uncovered_intersects_atomic:{covered_end}-{total_len}")
        else:
            # Append additional chunks for remainder with sentence-aware splitting
            while covered_end < total_len:
                # Overlap from previous
                overlap_from_prev = 0
                prefix = ""
                if chunks:
                    prev_s = chunks[-1]["offset_start"]
                    prev_e = chunks[-1]["offset_end"]
                    overlap_from_prev = min(cfg.overlap_chars, max(0, prev_e - prev_s))
                    if overlap_from_prev > 0:
                        prefix = text[prev_e - overlap_from_prev : prev_e]
                budget_unique = max(1, cfg.hard_max_chunk_chars - overlap_from_prev)
                target = min(total_len, covered_end + min(cfg.target_chunk_chars, budget_unique))
                # Try sentence boundary within budget
                rel = _sentence_split_pos(text[covered_end:target], target - covered_end)
                next_end = covered_end + (rel if rel > 0 else (target - covered_end))
                # As last resort, ensure we don't exceed hard_max
                if (next_end - covered_end) + overlap_from_prev > cfg.hard_max_chunk_chars:
                    next_end = covered_end + max(1, cfg.hard_max_chunk_chars - overlap_from_prev)
                chunk_text = prefix + text[covered_end:next_end]
                chunks.append(
                    {
                        "offset_start": covered_end,
                        "offset_end": next_end,
                        "text": chunk_text,
                        "overlap_from_prev": overlap_from_prev,
                        "block_kinds": list(
                            dict.fromkeys(
                                [
                                    b.kind
                                    for b in work_blocks
                                    if not (b.end <= covered_end or b.start >= next_end)
                                ]
                            )
                        )
                        or ["paragraph"],
                    }
                )
                covered_end = next_end

    return chunks, warnings


# -----------------------------
# Heading stack and section path
# -----------------------------


def _compute_section_paths(blocks: List[Block], chunks: List[dict[str, Any]]) -> List[List[str]]:
    paths: List[List[str]] = []
    stack: List[tuple[int, str]] = []  # (level, title)
    # walk blocks and map each offset to current path
    # build a list of (start, end, path)
    path_spans: List[tuple[int, int, List[str]]] = []
    for b in blocks:
        if b.kind == "heading":
            # Pop to level-1
            while stack and stack[-1][0] >= b.level:
                stack.pop()
            title = b.title or ""
            stack.append((b.level, title))
        path_spans.append((b.start, b.end, [t for _, t in stack]))

    def path_for_range(s: int, e: int) -> List[str]:
        # choose path at the start position: find the last span whose start <= s
        chosen: List[str] = []
        for bs, be, p in path_spans:
            if bs <= s < be:
                chosen = p
                break
            if bs <= s:
                chosen = p
        return chosen

    for ch in chunks:
        paths.append(path_for_range(ch["offset_start"], ch["offset_end"]))
    return paths


# -----------------------------
# Public API
# -----------------------------


def run_step2(inputs: Step2Inputs) -> Dict[str, Any]:
    t0 = time.time()
    cfg = inputs.config or Step2Config()
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
        # Persist minimal result
        _persist_step2(doc_uid, result, chunks=None, stats=None)
        return result

    # Validate hash and uid
    canonical_meta = _canonical_meta_from_step1(step1)
    recomputed_hash = _compute_content_hash(normalized_text, canonical_meta)
    step1_hash = step1.get("content_hash")
    if inputs.content_hash and inputs.content_hash != step1_hash:
        result = {"status": "failed", "errors": [{"code": "hash_mismatch"}]}
        _persist_step2(doc_uid, result, chunks=None, stats=None)
        return result
    if step1_hash != recomputed_hash:
        result = {"status": "failed", "errors": [{"code": "hash_mismatch"}]}
        _persist_step2(doc_uid, result, chunks=None, stats=None)
        return result

    # Parse blocks
    blocks = _parse_blocks(normalized_text)

    # Assemble chunks
    chunks_basic, warnings = _assemble_chunks(normalized_text, blocks, cfg)

    # Attach derived fields
    section_paths = _compute_section_paths(blocks, chunks_basic)
    chunks: List[Dict[str, Any]] = []
    total_unique = 0
    covered_intervals: List[tuple[int, int]] = []
    for idx, ch in enumerate(chunks_basic):
        text_bytes = ch["text"].encode("utf-8")
        checksum = hashlib.sha256(text_bytes).hexdigest()
        char_count = len(ch["text"])  # chars
        chunks.append(
            {
                "chunk_id": f"{doc_uid}_{idx:04d}",
                "chunk_index": idx,
                "text": ch["text"],
                "offset_start": ch["offset_start"],
                "offset_end": ch["offset_end"],
                "char_count": char_count,
                "tokens_count": None,
                "overlap_from_prev": ch["overlap_from_prev"],
                "section_path": section_paths[idx],
                "block_kinds": ch["block_kinds"],
                "checksum_sha256": checksum,
            }
        )
        covered_intervals.append((ch["offset_start"], ch["offset_end"]))

    # Merge intervals to compute unique coverage
    covered_intervals.sort()
    merged: List[tuple[int, int]] = []
    for s, e in covered_intervals:
        if not merged or s > merged[-1][1]:
            merged.append((s, e))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
    for s, e in merged:
        total_unique += max(0, e - s)

    total_chars = len(normalized_text)
    stats = {
        "total_chars": total_chars,
        "total_chunks": len(chunks),
        "avg_chars": int(sum(c["char_count"] for c in chunks) / len(chunks)) if chunks else 0,
        "max_chars": max((c["char_count"] for c in chunks), default=0),
        "min_chars": min((c["char_count"] for c in chunks), default=0),
        "coverage_ratio": (total_unique / total_chars) if total_chars > 0 else 0.0,
    }

    t1 = time.time()

    processing_report = {
        "run_id": run_id,
        "timings_ms": {
            "segment": int((t1 - t0) * 1000),
        },
        "warnings": warnings,
    }

    # QA checks
    errors: List[dict[str, str]] = []
    # No empty chunks and bounds
    for c in chunks:
        if c["char_count"] <= 0:
            errors.append({"code": "empty_chunk"})
        if c["char_count"] > cfg.hard_max_chunk_chars:
            errors.append({"code": "chunk_exceeds_hard_max"})
        if c["overlap_from_prev"] > cfg.overlap_chars:
            errors.append({"code": "overlap_exceeds_config"})
    # Atomic size failure
    for b in blocks:
        if b.kind in {"code", "table"} and (b.end - b.start) > cfg.hard_max_chunk_chars:
            errors.append({"code": "atomic_block_too_large"})
    # Coverage
    if stats["coverage_ratio"] < 0.999:
        errors.append({"code": "insufficient_coverage"})

    # Determinism check: rerun assembly and compare
    chunks2_basic, _ = _assemble_chunks(normalized_text, blocks, cfg)
    same = len(chunks_basic) == len(chunks2_basic) and all(
        cb["offset_start"] == cb2["offset_start"]
        and cb["offset_end"] == cb2["offset_end"]
        and cb["overlap_from_prev"] == cb2["overlap_from_prev"]
        and cb["block_kinds"] == cb2["block_kinds"]
        for cb, cb2 in zip(chunks_basic, chunks2_basic)
    )
    if not same:
        errors.append({"code": "nondeterministic"})

    output: Dict[str, Any] = {
        "document_uid": doc_uid,
        "content_hash": step1_hash,
        "chunking_policy_version": CHUNKING_POLICY_VERSION,
        "chunks": chunks,
        "stats": stats,
        "processing_report": processing_report,
        "status": "ok" if not errors else "failed",
    }

    if errors:
        output["errors"] = errors

    # Persist artifacts
    _persist_step2(doc_uid, output, chunks=chunks if not errors else None, stats=stats)

    # Structured log line (no content)
    logger.info(
        "step2 %s: uid=%s chunks=%d total_chars=%d coverage=%.6f warnings=%d",
        output["status"],
        doc_uid,
        len(chunks),
        total_chars,
        stats["coverage_ratio"],
        len(warnings),
    )

    return output


# -----------------------------
# Persistence
# -----------------------------


def _persist_step2(
    doc_uid: str,
    output: dict[str, Any],
    *,
    chunks: List[dict[str, Any]] | None,
    stats: dict[str, Any] | None,
) -> None:
    step2_dir = ARTIFACTS_ROOT / doc_uid / "step2"
    step2_dir.mkdir(parents=True, exist_ok=True)

    # Always write result.json
    (step2_dir / "result.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if output.get("status") != "ok":
        return

    # chunks.jsonl
    if chunks is not None:
        with (step2_dir / "chunks.jsonl").open("w", encoding="utf-8") as f:
            for c in chunks:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
    # stats.json
    if stats is not None:
        (step2_dir / "stats.json").write_text(
            json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # Optional per-chunk files for QA
    chunks_dir = step2_dir / "chunks"
    chunks_dir.mkdir(exist_ok=True)
    for c in chunks or []:
        (chunks_dir / f"{c['chunk_index']:04d}.txt").write_text(c["text"], encoding="utf-8")
