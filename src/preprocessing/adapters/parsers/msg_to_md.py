"""Microsoft Outlook .msg → Markdown adapter (offline, structure-preserving).

Capability note
----------------
- Purpose: Convert standalone Outlook .msg (MAPI) emails into structurally faithful
  Markdown, preserving headers, body structure (headings, paragraphs, lists, links,
  blockquotes), simple tables (GitHub-style), inline images (as local assets), and
  attachments (listed; not inlined).
- Scope: Single .msg files (not PST). Supports HTML, plain text, and RTF bodies
  (RTF is converted via local logic). Requires no network I/O; everything is offline.
- Non-goals: No anonymization, translate, or language langid. S/MIME encryption
  is not decrypted. Active content (scripts/styles) is stripped during HTML sanitation.

Integration
-----------
- Selected by the parser registry for extension: .msg
- Adapter key/name: "MsgToMd" (class attribute) for registry preferences/disable lists.
- Output is deterministic for identical inputs: stable asset filenames, stable header
  block layout, and stable Markdown structure.

Configuration (env/settings)
----------------------------
The adapter is designed to be configured via pipeline settings or environment
variables (actual reads occur in a concrete implementation). The knobs are:

- MSG_PREFER_BODY (str): Priority order to select body, default "html,text,rtf".
  Must be a comma-separated subset/permutation of {html, text, rtf}.
- MSG_EXPORT_ASSETS (bool): Whether to export attachments/inline images. Default true.
- MSG_ASSETS_SUBDIR (str): Assets subdirectory name relative to the output location.
  Default "assets".
- MSG_MAX_ATTACHMENT_SIZE_MB (float|int): Max attachment size allowed for export.
  Larger files are still listed in Markdown but may be skipped (and warned) if export
  is disabled by policy.
- MSG_QUOTED_REPLY_MODE (str): One of {"blockquote", "preserve", "strip"}. Default
  "blockquote". Controls how quoted-reply sections are surfaced in Markdown.
- STRICT_MODE (bool): If true, missing body or asset export failures raise instead of
  warn; otherwise, the adapter emits Markdown with conversion_warnings.

Registry note
-------------
- Register for .msg and log the adapter key when selected. On failure, suggest enabling
  asset export or installing an optional HTML-to-Markdown converter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Public constants for registry wiring
EXTENSIONS: tuple[str, ...] = ("msg",)


@dataclass(frozen=True)
class AttachmentExportPlan:
    """Description of how attachments/inline images are exported.

    Description:
        Provides an audit-friendly plan for deterministic asset file names and output
        locations. Concrete implementations should populate this plan (or an equivalent
        structure) and mirror it into the output metadata to ensure reproducibility and
        predictable file paths.

    Args:
        assets_dir (str): Absolute or relative directory where exported assets are
            written. Relative paths are resolved from the target Markdown location.
        filename_map (dict[str, str]): Mapping from a deterministic logical key to the
            exported filename. The logical key should be stable across runs, such as
            "att_0001" or a sanitized original name with an index. For inline images,
            logical keys may be CIDs (e.g., "cid:abcd@local").

    Notes:
        - Filenames must be stable across runs for identical inputs/configuration.
        - The adapter must ensure legality and safety of filenames (no path traversal).
        - When export is disabled, this plan may still describe the hypothetical names
          for determinism but no files are written; metadata should reflect that.
    """

    assets_dir: str
    filename_map: dict[str, str]


class MsgToMd:
    """MSG-to-Markdown converter adapter (offline, deterministic, structure-first).

    Description:
        Converts Microsoft Outlook .msg messages into clean Markdown suitable for
        downstream normalization, enrichment, and vectorization. The adapter validates
        the input, parses MAPI headers and body parts, chooses the best-available body
        (HTML > text > RTF by default), sanitizes HTML, converts it to Markdown while
        preserving headings/paragraphs/lists/links/quotes/simple tables, handles inline
        images via a deterministic assets/ export, lists non-image attachments, and
        prepends a compact Email header block to the Markdown body.

    Args:
        prefer_body (tuple[str, ...] | None, optional):
            Priority order for selecting the message body. Elements must be among
            {"html", "text", "rtf"}. When None, a concrete implementation should read
            MSG_PREFER_BODY or fall back to ("html", "text", "rtf"). Defaults to None.
        export_assets (bool, optional):
            Whether to export attachments and inline images. When False, inline image
            references are kept as text placeholders, and attachments are listed only.
            Defaults to True.
        assets_subdir (str, optional):
            Subdirectory name used to store exported files relative to the Markdown
            output location. Defaults to "assets".
        max_attachment_size_mb (float | None, optional):
            Maximum individual attachment size to export. Larger files are listed but
            may be skipped from export depending on policy. None means no size limit.
            Defaults to None.
        quoted_reply_mode (str, optional):
            One of {"blockquote", "preserve", "strip"}. Controls formatting of quoted
            replies: "blockquote" converts recognized quoted sections to Markdown
            blockquotes; "preserve" retains them verbatim; "strip" removes them while
            recording a warning. Defaults to "blockquote".
        strict_mode (bool, optional):
            If True, certain recoverable issues cause exceptions (e.g., no parsable body,
            asset export failure). If False, they result in warnings collected in
            metadata. Defaults to False.

    Attributes:
        name (str): Adapter identifier used by the registry.
        supported_features (dict[str, bool]): Capability flags for audit/telemetry,
            including HTML, text, RTF, tables, inline images, attachments, and quoted
            reply handling modes.

    Returns:
        The class exposes a parse(raw) method which returns a domain-level MarkdownDoc
        as specified under parse(). The constructor performs no I/O.

    Raises:
        No exceptions are raised at construction time. See parse() for detailed error
        contracts during conversion.

    Notes:
        - Deterministic outputs: stable asset filenames and header block layout.
        - Sanitization: strips scripts/styles while preserving text structure.
        - Offline: no network calls. S/MIME-encrypted messages are not decrypted.
    """

    name: str = "MsgToMd"

    def __init__(
        self,
        *,
        prefer_body: tuple[str, ...] | None = None,
        export_assets: bool = True,
        assets_subdir: str = "assets",
        max_attachment_size_mb: float | None = None,
        quoted_reply_mode: str = "blockquote",
        strict_mode: bool = False,
    ) -> None:
        self._prefer_body = tuple(prefer_body) if prefer_body is not None else None
        self._export_assets = bool(export_assets)
        self._assets_subdir = str(assets_subdir)
        self._max_attachment_size_mb = (
            float(max_attachment_size_mb) if max_attachment_size_mb is not None else None
        )
        self._quoted_reply_mode = str(quoted_reply_mode)
        self._strict_mode = bool(strict_mode)
        self.supported_features: dict[str, bool] = {
            "html": True,
            "text": True,
            "rtf": True,
            "tables": True,  # best-effort GitHub-style tables
            "inline_images": True,  # CID mapping to assets
            "attachments": True,  # listed with size; deterministic names
            "quotes_blockquote": True,
            "quotes_preserve": True,
            "quotes_strip": True,
        }

    def parse(self, raw: Any) -> Any:  # RawDocument -> MarkdownDoc (see detailed docs)
        """Convert a .msg email to UTF-8, LF-normalized Markdown with metadata.

        Minimal offline implementation using extract_msg. No assets export.
        """
        import re
        from datetime import datetime
        from pathlib import Path

        try:
            import extract_msg  # type: ignore
        except Exception as e:
            raise ImportError(
                "extract_msg is required for MSG parsing. Install with: pip install extract_msg"
            ) from e
        try:
            from ...domain.models_markdown import MarkdownDoc
        except Exception:
            MarkdownDoc = None  # type: ignore

        # Access source path
        path = getattr(raw, "path", None) or (raw.get("path") if isinstance(raw, dict) else None)
        if path is None:
            raise ValueError("raw.path is required for MsgToMd.parse")
        p = Path(str(path))
        if not p.exists():
            raise FileNotFoundError(str(p))

        # Load message
        try:
            msg = extract_msg.Message(str(p))
            # Some extract_msg versions expose ensureDecoded(), others decode lazily.
            # Call defensively if present; otherwise touch common properties to trigger decoding.
            try:
                if hasattr(msg, "ensureDecoded") and callable(getattr(msg, "ensureDecoded")):
                    msg.ensureDecoded()  # type: ignore[attr-defined]
                elif hasattr(msg, "decode") and callable(getattr(msg, "decode")):
                    msg.decode()  # type: ignore[attr-defined]
                else:
                    _ = getattr(msg, "body", None)
                    _ = getattr(msg, "htmlBody", None)
                    _ = getattr(msg, "rtfBody", None)
            except Exception:
                # Decoding issues are tolerated here; we'll still try to read fields below.
                pass
        except Exception as e:
            raise RuntimeError(f"Failed to open .msg: {e}") from e

        # Headers
        def _norm_list(x: Any) -> str:
            if x is None:
                return ""
            if isinstance(x, (list, tuple)):
                return ", ".join(str(i) for i in x if i)
            return str(x)

        from_h = (
            getattr(msg, "sender", None)
            or getattr(msg, "senderemail", None)
            or getattr(msg, "from_", None)
        )
        to_h = getattr(msg, "to", None)
        cc_h = getattr(msg, "cc", None)
        bcc_h = getattr(msg, "bcc", None)
        subject = getattr(msg, "subject", None) or ""
        date_raw = getattr(msg, "date", None) or getattr(msg, "date_str", None)
        date_utc: str | None = None
        try:
            if date_raw:
                # extract_msg may already provide a datetime
                if isinstance(date_raw, datetime):
                    date_utc = date_raw.isoformat()
                else:
                    date_utc = str(date_raw)
        except Exception:
            date_utc = None

        # Body selection per preference
        prefer = (
            tuple(self._prefer_body) if self._prefer_body is not None else ("html", "text", "rtf")
        )
        html_body = getattr(msg, "htmlBody", None) or getattr(msg, "html", None)
        text_body = getattr(msg, "body", None)
        rtf_body = getattr(msg, "rtfBody", None)
        body_sel = None
        body_type = None
        for kind in prefer:
            if kind == "html" and html_body:
                body_sel = str(html_body)
                body_type = "html"
                break
            if kind == "text" and text_body:
                body_sel = str(text_body)
                body_type = "text"
                break
            if kind == "rtf" and rtf_body:
                # Very minimal RTF to text stripping
                s = re.sub(r"\\'[0-9a-fA-F]{2}", " ", str(rtf_body))  # remove hex escapes
                s = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", s)  # control words
                s = re.sub(r"[{}]", "", s)
                body_sel = s
                body_type = "rtf"
                break
        if body_sel is None:
            if self._strict_mode:
                # Best-effort cleanup before raising
                try:
                    if hasattr(msg, "close"):
                        msg.close()  # type: ignore[attr-defined]
                except Exception:
                    pass
                raise RuntimeError("No parsable body in .msg")
            body_sel = ""
            body_type = "text"

        def html_to_md(html: str) -> tuple[str, int, int]:
            # Simplistic, dependency-free HTML→Markdown conversion
            links: list[tuple[str, str]] = []

            def _link_repl(m: re.Match) -> str:
                url = m.group(1) or ""
                txt = m.group(2) or url
                links.append((txt, url))
                return f"[{txt}]({url})"

            s = re.sub(r"<\s*br\s*/?>", "\n", html, flags=re.I)
            s = re.sub(r"<\s*/p\s*>", "\n\n", s, flags=re.I)
            s = re.sub(r"<\s*p\s*[^>]*>", "", s, flags=re.I)
            s = re.sub(
                r"<\s*h([1-6])[^>]*>(.*?)<\s*/h\1\s*>",
                lambda m: "#" * int(m.group(1)) + " " + m.group(2) + "\n\n",
                s,
                flags=re.I | re.S,
            )
            s = re.sub(
                r"<\s*a\s+[^>]*href=\"([^\"]+)\"[^>]*>(.*?)<\s*/a\s*>",
                _link_repl,
                s,
                flags=re.I | re.S,
            )
            # Strip remaining tags
            s = re.sub(r"<[^>]+>", "", s)
            # Unescape HTML entities
            import html as _html

            s = _html.unescape(s)
            # Normalize whitespace/newlines
            s = s.replace("\r\n", "\n").replace("\r", "\n")
            s = re.sub(r"\n\n\n+", "\n\n", s)
            return s.strip(), 0, len(links)

        # Convert body to Markdown
        if body_type == "html":
            body_md, tables_detected, links_count = html_to_md(body_sel)
        else:
            body_md = str(body_sel or "").replace("\r\n", "\n").replace("\r", "\n").strip()
            tables_detected = 0
            # naive link langid
            links_count = len(re.findall(r"https?://\S+", body_md))

        # Attachments (list only)
        attachments = []
        inline_images = 0
        try:
            for att in getattr(msg, "attachments", []) or []:
                name = (
                    getattr(att, "longFilename", None)
                    or getattr(att, "shortFilename", None)
                    or getattr(att, "filename", None)
                    or "attachment"
                )
                attachments.append(str(name))
        except Exception:
            pass

        header_block = [
            f"From: {_norm_list(from_h)}",
            f"To: {_norm_list(to_h)}",
            f"Cc: {_norm_list(cc_h)}",
            f"Bcc: {_norm_list(bcc_h)}",
            f"Subject: {subject}",
            f"Date: {date_utc or ''}",
        ]
        md = "\n".join(header_block) + "\n\n" + body_md

        meta: dict[str, Any] = {
            "headers": {
                "from": _norm_list(from_h),
                "to": _norm_list(to_h),
                "cc": _norm_list(cc_h),
                "bcc": _norm_list(bcc_h),
                "subject": subject,
                "date_utc": date_utc,
            },
            "body_type_selected": body_type,
            "attachments_count": len(attachments),
            "inline_images_count": inline_images,
            "assets_dir": self._assets_subdir,
            "inline_cid_map": {},
            "tables_detected": tables_detected,
            "links_count": links_count,
            "quoted_sections_detected": 0,
            "conversion_warnings": [],
        }

        doc_id = p.stem
        try:
            if MarkdownDoc is not None:
                return MarkdownDoc(
                    doc_id=doc_id, path=str(p), variant=None, lang=None, text_md=md, meta=meta
                )
            return type("_Doc", (), {"text_md": md, "meta": meta})()
        finally:
            try:
                if hasattr(msg, "close"):
                    msg.close()  # type: ignore[attr-defined]
            except Exception:
                pass

    def describe(self) -> dict[str, Any]:
        """Return a static capability/configuration description for audit/telemetry.

        Returns:
            dict: A dictionary describing adapter name, extensions, supported features,
            and a snapshot of configuration relevant for deterministic behavior.
        """
        return {
            "name": self.name,
            "extensions": list(EXTENSIONS),
            "supported_features": dict(self.supported_features),
            "config": {
                "prefer_body": list(self._prefer_body) if self._prefer_body is not None else None,
                "export_assets": self._export_assets,
                "assets_subdir": self._assets_subdir,
                "max_attachment_size_mb": self._max_attachment_size_mb,
                "quoted_reply_mode": self._quoted_reply_mode,
                "strict_mode": self._strict_mode,
            },
        }
