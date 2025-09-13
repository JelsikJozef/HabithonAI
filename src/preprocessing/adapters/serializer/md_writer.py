"""
Deterministic Markdown serializer with atomic writes, assets management, and metadata policies.

Capabilities:
- Maps a document's source path under an output root preserving relative structure from src_root.
- Writes Markdown atomically (UTF-8, LF) and manages an adjacent assets directory.
- Emits sidecar JSON or inline metadata per policy; supports dry-run planning.
- Safe and deterministic: path traversal prevention, Windows-safe filenames, idempotent outputs.

This module performs local filesystem operations only; no network access.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

__all__ = [
    "MarkdownSerializerPort",
    "WriterContext",
    "TargetPaths",
    "WriteResult",
    "WriteError",
    "MarkdownWriter",
    "descriptor",
]


# -----------------
# Errors
# -----------------


class WriteError(RuntimeError):
    """Raised for actionable write-time failures.

    Args:
        code: Stable error code (e.g., "path_violation", "permission_error", "io_error").
        message: Human-readable message with path and remediation when possible.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# -----------------
# Data objects
# -----------------


@dataclass(frozen=True)
class WriterContext:
    """Immutable context controlling write behavior and path mapping.

    Attributes:
        out_root: Absolute output root directory. All outputs must be under this tree.
        src_root: Absolute source root used to compute relative structure for mapping.
        assets_subdir: Directory name under the output tree to hold assets (e.g., "assets").
        assets_layout: Assets layout policy: "per_doc" creates a per-document folder
            under assets_subdir using the document stem; "flat" uses a single folder.
        write_meta: Metadata policy: "none", "sidecar", or "inline".
        overwrite: Whether to overwrite existing .md and sidecar files.
        dry_run: If True, no filesystem changes are performed; write() returns a plan
            with status="dry_run" and bytes fields set to None.
        ensure_final_newline: If True/False, enforce a single terminal LF; if None,
            inherit upstream policy (content is written as given).
    """

    out_root: str
    src_root: str
    assets_subdir: str
    assets_layout: Literal["per_doc", "flat"] = "per_doc"
    write_meta: Literal["none", "sidecar", "inline"] = "sidecar"
    overwrite: bool = False
    dry_run: bool = False
    ensure_final_newline: bool | None = None


@dataclass(frozen=True)
class TargetPaths:
    """Deterministic target paths for a document write operation.

    Attributes:
        out_md_path: Absolute path of the Markdown file to write.
        assets_dir: Absolute path of the assets directory for this document.
        sidecar_meta_path: Absolute path of the sidecar metadata file when
            write_meta="sidecar"; otherwise None.
    """

    out_md_path: str
    assets_dir: str
    sidecar_meta_path: str | None


@dataclass(frozen=True)
class WriteResult:
    """JSON-serializable result of a write operation.

    Attributes:
        status: One of {"ok", "skip_existing", "dry_run"}.
        out_md_path: Absolute path of the Markdown file.
        assets_dir: Absolute path of the assets directory or None when not applicable.
        assets_written: Number of assets created during this write.
        bytes_written_md: Size in bytes of the Markdown file content written; None in dry-run.
        bytes_written_assets: Sum of asset bytes written; None in dry-run.
        sidecar_written: Whether a sidecar metadata file was created/overwritten.
        renamed_assets: List of name mappings when collisions were resolved deterministically.
        warnings: List of non-fatal anomalies encountered (e.g., sanitized filename).
        error: Optional error payload {code, message} for non-exceptional failure modes; None on success.

    Notes:
        - All paths are absolute. Counts reflect writes performed in this call.
        - In dry-run, no filesystem side effects occur and bytes fields are None.
    """

    status: Literal["ok", "skip_existing", "dry_run"]
    out_md_path: str
    assets_dir: str | None
    assets_written: int
    bytes_written_md: int | None
    bytes_written_assets: int | None
    sidecar_written: bool
    renamed_assets: list[dict[str, str]]
    warnings: list[str]
    error: dict[str, str] | None


# -----------------
# Port and concrete implementation
# -----------------


class MarkdownSerializerPort:
    """Serializer contract for writing a MarkdownDoc to disk deterministically.

    Subclasses must implement write() and compute_paths(). See MarkdownWriter
    for a reference implementation.
    """

    def write(
        self, doc: Any, ctx: WriterContext
    ) -> WriteResult:  # pragma: no cover - interface only
        raise NotImplementedError

    def compute_paths(
        self, doc: Any, ctx: WriterContext
    ) -> TargetPaths:  # pragma: no cover - interface only
        raise NotImplementedError


class MarkdownWriter(MarkdownSerializerPort):
    """Deterministic Markdown writer with atomic writes and assets handling.

    Description:
        Maps a document's source path under out_root by preserving relative
        structure from src_root. Writes the Markdown payload atomically using
        UTF-8 with LF newlines (content is not mutated except optional final
        newline). Creates an assets directory deterministically and handles
        parser-produced assets (copy/write modes). Emits sidecar JSON or an
        inline metadata block per policy. All outputs are platform-safe and
        path traversal is prevented.

    Notes:
        - Overwrite policy: when overwrite=True, the Markdown file and sidecar are
          replaced atomically; the assets directory is cleared and recreated.
        - Dry-run performs no writes and returns a plan with status="dry_run".
        - This writer is safe for file-level concurrency; it holds no shared state.
    """

    # Windows reserved basenames (case-insensitive)
    _WIN_RESERVED = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }

    def write(self, doc: Any, ctx: WriterContext) -> WriteResult:  # type: ignore[override]
        text = _get_doc_text(doc)
        src_path = _get_doc_path(doc)
        meta = _get_doc_meta(doc)
        targets = self.compute_paths(doc, ctx)
        warnings: list[str] = []
        renamed: list[dict[str, str]] = []

        out_md = Path(targets.out_md_path)
        assets_dir = Path(targets.assets_dir)
        sidecar = Path(targets.sidecar_meta_path) if targets.sidecar_meta_path else None

        if ctx.dry_run:
            return WriteResult(
                status="dry_run",
                out_md_path=str(out_md),
                assets_dir=str(assets_dir),
                assets_written=0,
                bytes_written_md=None,
                bytes_written_assets=None,
                sidecar_written=False,
                renamed_assets=renamed,
                warnings=warnings,
                error=None,
            )

        # Create parent directories
        out_md.parent.mkdir(parents=True, exist_ok=True)

        # Overwrite/skip policy for .md
        if out_md.exists() and not ctx.overwrite:
            return WriteResult(
                status="skip_existing",
                out_md_path=str(out_md),
                assets_dir=str(assets_dir),
                assets_written=0,
                bytes_written_md=out_md.stat().st_size if out_md.exists() else 0,
                bytes_written_assets=0,
                sidecar_written=False,
                renamed_assets=renamed,
                warnings=warnings,
                error=None,
            )

        # Ensure final newline if mandated
        content = text
        if ctx.ensure_final_newline is True:
            if not content.endswith("\n"):
                content += "\n"
        elif ctx.ensure_final_newline is False:
            # Ensure at most one terminal LF
            while content.endswith("\n\n"):
                content = content[:-1]

        # Atomic write of Markdown
        bytes_md = _atomic_write_text(out_md, content)

        # Assets handling
        assets_written = 0
        bytes_assets = 0
        if ctx.overwrite and assets_dir.exists():
            _clear_dir(assets_dir)
        assets_dir.mkdir(parents=True, exist_ok=True)
        # Copy mode
        copy_list = meta.get("assets_to_copy") if isinstance(meta, Mapping) else None
        if isinstance(copy_list, Sequence):
            for src in copy_list:
                try:
                    srcp = Path(str(src))
                    name_sanit = _sanitize_filename(srcp.name)
                    dest = assets_dir / name_sanit
                    dest = _resolve_conflict(dest, assets_dir, renamed)
                    shutil.copy2(srcp, dest)
                    assets_written += 1
                    try:
                        bytes_assets += dest.stat().st_size
                    except Exception:
                        pass
                except Exception as e:
                    warnings.append(f"asset_copy_failed: {src}: {e}")
        # Write mode
        write_list = meta.get("assets_to_write") if isinstance(meta, Mapping) else None
        if isinstance(write_list, Sequence):
            for item in write_list:
                try:
                    if not isinstance(item, Mapping):
                        warnings.append("asset_write_skipped: invalid spec type")
                        continue
                    name = _sanitize_filename(str(item.get("name") or "asset"))
                    data = item.get("bytes") or item.get("data")
                    if not isinstance(data, (bytes, bytearray)):
                        warnings.append(f"asset_write_skipped: {name}: missing bytes")
                        continue
                    dest = assets_dir / name
                    dest = _resolve_conflict(dest, assets_dir, renamed)
                    _atomic_write_bytes(dest, bytes(data))
                    assets_written += 1
                    bytes_assets += len(data)
                except Exception as e:
                    warnings.append(f"asset_write_failed: {item.get('name', 'asset')}: {e}")

        # Sidecar or inline metadata
        sidecar_written = False
        if ctx.write_meta == "sidecar":
            if sidecar is None:
                sidecar = out_md.with_suffix(out_md.suffix + ".meta.json")
            payload = _build_sidecar_payload(doc, ctx)
            _atomic_write_json(sidecar, payload)
            sidecar_written = True
        elif ctx.write_meta == "inline":
            # Append a small fenced json block
            inline = _build_inline_metadata(doc, ctx)
            with out_md.open("a", encoding="utf-8", newline="\n") as f:
                f.write("\n\n" + inline)
            try:
                bytes_md = out_md.stat().st_size
            except Exception:
                pass

        return WriteResult(
            status="ok",
            out_md_path=str(out_md),
            assets_dir=str(assets_dir),
            assets_written=assets_written,
            bytes_written_md=bytes_md,
            bytes_written_assets=bytes_assets,
            sidecar_written=sidecar_written,
            renamed_assets=renamed,
            warnings=warnings,
            error=None,
        )

    def compute_paths(self, doc: Any, ctx: WriterContext) -> TargetPaths:  # type: ignore[override]
        src_path = _get_doc_path(doc)
        out_root = Path(ctx.out_root)
        src_root = Path(ctx.src_root)
        if not out_root.is_absolute() or not src_root.is_absolute():
            raise WriteError("path_violation", "out_root and src_root must be absolute paths")

        # Compute relative path from src_root
        try:
            rel = Path(src_path).resolve().relative_to(src_root.resolve())
        except Exception:
            # Fall back to using only the basename if outside src_root
            rel = Path(src_path).name
        rel = Path(*map(_sanitize_path_component, rel.parts))

        out_dir = out_root / rel.parent
        out_dir = _ensure_within_root(out_root, out_dir)
        stem = _sanitize_filename(rel.stem)
        out_md = (out_dir / f"{stem}.md").resolve()
        out_md = _ensure_within_root(out_root, out_md)

        # Assets directory
        if ctx.assets_layout == "per_doc":
            assets_dir = out_dir / ctx.assets_subdir / stem
        else:
            assets_dir = out_dir / ctx.assets_subdir
        assets_dir = assets_dir.resolve()
        assets_dir = _ensure_within_root(out_root, assets_dir)

        sidecar = None
        if ctx.write_meta == "sidecar":
            sidecar = str((out_md.parent / (out_md.stem + ".meta.json")).resolve())

        return TargetPaths(
            out_md_path=str(out_md),
            assets_dir=str(assets_dir),
            sidecar_meta_path=sidecar,
        )


# -----------------
# Utilities
# -----------------


def descriptor(ctx: WriterContext) -> str:
    """Return a short writer descriptor for logging.

    Example:
        md_writer(out=/abs/out, assets=assets/per_doc, meta=sidecar, overwrite=False)
    """
    assets = f"{ctx.assets_subdir}/{'per_doc' if ctx.assets_layout=='per_doc' else 'flat'}"
    return (
        "md_writer("
        f"out={ctx.out_root}, "
        f"assets={assets}, "
        f"meta={ctx.write_meta}, "
        f"overwrite={ctx.overwrite}"
        ")"
    )


def _get_doc_text(doc: Any) -> str:
    text = getattr(doc, "text_md", getattr(doc, "text", None))
    if not isinstance(text, str):
        raise WriteError("invalid_doc", "doc.text_md (or .text) must be a string")
    # Do not mutate text beyond optional final newline in writer; upstream normalizer enforces LF.
    return text


def _get_doc_path(doc: Any) -> str:
    p = getattr(doc, "path", None)
    if not p:
        # Also accept meta.source.path
        meta = getattr(doc, "meta", {}) or {}
        p = (meta.get("source") or {}).get("path") if isinstance(meta, Mapping) else None
    if not p:
        raise WriteError("invalid_doc", "doc.path is required to compute target mapping")
    return str(p)


def _get_doc_meta(doc: Any) -> Mapping[str, Any]:
    m = getattr(doc, "meta", {})
    try:
        return MappingProxyType(m)  # type: ignore[name-defined]
    except Exception:
        return m if isinstance(m, Mapping) else {}


def _sanitize_path_component(name: str) -> str:
    # Apply filename sanitization, but keep separators out of this function's inputs.
    return _sanitize_filename(name)


def _sanitize_filename(name: str, *, max_len: int = 128) -> str:
    # Remove path separators and control characters
    name = re.sub(r"[\\/\x00-\x1F]", "_", name)
    # Strip unsafe characters on Windows and common shells
    name = re.sub(r"[<>:" "|?*]", "_", name)
    # Collapse whitespace
    name = re.sub(r"\s+", " ", name).strip()
    # Prevent trailing dots/spaces (Windows)
    name = name.rstrip(" .")
    if not name:
        name = "untitled"
    # Reserved device names
    base, dot, ext = name.partition(".")
    if base.upper() in MarkdownWriter._WIN_RESERVED:
        base = f"_{base}"
    name = base + (dot + ext if dot else "")
    # Enforce max length
    if len(name) > max_len:
        # Keep extension if present
        if dot:
            keep = max_len - len(dot + ext)
            name = base[: max(1, keep)] + dot + ext
        else:
            name = name[:max_len]
    return name


def _ensure_within_root(root: Path, target: Path) -> Path:
    try:
        root_r = root.resolve()
        targ_r = target.resolve()
        # Python 3.12+: Path.is_relative_to; use os.path.commonpath for older
        if os.path.commonpath([str(root_r), str(targ_r)]) != str(root_r):
            raise WriteError("path_violation", f"Target escapes out_root: {targ_r}")
        return targ_r
    except WriteError:
        raise
    except Exception as e:  # pragma: no cover - defensive
        raise WriteError("path_violation", f"Failed to validate target path: {e}")


def _atomic_write_text(path: Path, content: str) -> int:
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        _fsync_dir(path.parent)
        return path.stat().st_size
    except Exception:
        # Attempt cleanup on failure
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass
        raise


def _atomic_write_bytes(path: Path, data: bytes) -> int:
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        _fsync_dir(path.parent)
        return path.stat().st_size
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass
        raise


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> int:
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, ensure_ascii=False, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        _fsync_dir(path.parent)
        return path.stat().st_size
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass
        raise


def _fsync_dir(dir_path: Path) -> None:
    try:
        fd = os.open(str(dir_path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception:  # pragma: no cover - best-effort
        pass


def _resolve_conflict(dest: Path, folder: Path, renamed: list[dict[str, str]]) -> Path:
    if not dest.exists():
        return dest
    stem = dest.stem
    suffix = dest.suffix
    i = 1
    while True:
        cand = folder / f"{stem}_{i}{suffix}"
        if not cand.exists():
            renamed.append({"from": dest.name, "to": cand.name})
            return cand
        i += 1


def _clear_dir(path: Path) -> None:
    if not path.exists():
        return
    for p in path.iterdir():
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink(missing_ok=True)  # type: ignore[call-arg]
        except Exception:
            # Best-effort cleanup
            pass


def _build_sidecar_payload(doc: Any, ctx: WriterContext) -> Mapping[str, Any]:
    meta = getattr(doc, "meta", {}) or {}
    # Compact subset with safe fields only; redact unknown secrets by default
    src_rel = None
    try:
        src_rel = str(Path(_get_doc_path(doc)).resolve().relative_to(Path(ctx.src_root).resolve()))
    except Exception:
        src_rel = _get_doc_path(doc)
    normalization = {}
    if isinstance(meta, Mapping):
        norm = meta.get("normalization")
        if isinstance(norm, Mapping):
            # Include summary and key counters only
            normalization = {
                "summary": norm.get("summary"),
                "bom": bool(norm.get("bom_removed", False)),
                "eol": norm.get("eol_after"),
                "unicode": norm.get("unicode_form_after"),
                "trimmed": norm.get("trailing_spaces_trimmed"),
                "tabs": norm.get("tabs_converted"),
            }
    payload = {
        "source": {"relative_to_src_root": src_rel},
        "counts": {
            "assets_to_copy": (
                len(meta.get("assets_to_copy", [])) if isinstance(meta, Mapping) else 0
            ),
            "assets_to_write": (
                len(meta.get("assets_to_write", [])) if isinstance(meta, Mapping) else 0
            ),
        },
        "normalization": normalization,
        "redacted": True if meta.get("secrets", None) else False,
    }
    return payload


def _build_inline_metadata(doc: Any, ctx: WriterContext) -> str:
    payload = _build_sidecar_payload(doc, ctx)
    # Build fenced json block, compact
    buf = io.StringIO()
    buf.write("## Metadata\n\n")
    buf.write("```json\n")
    buf.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    buf.write("\n```\n")
    return buf.getvalue()
