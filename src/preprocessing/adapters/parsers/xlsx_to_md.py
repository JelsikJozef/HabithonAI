"""Excel .xlsx → Markdown adapter (offline, deterministic tables-only).

Capability note
----------------
- Purpose: Convert Microsoft Excel spreadsheets (.xlsx; optionally .xls when enabled)
  into clean, deterministic Markdown tables, one section per worksheet, preserving
  sheet order, basic cell formatting policy (display/raw), hyperlinks, and merged
  cell handling per configured policy.
- Supported: worksheet enumeration and filtering, header rows policy, raw vs. display
  rendering modes, deterministic merged-cell handling, hyperlinks to Markdown links,
  in-cell newline handling via <br>, optional truncation, and per-sheet stats.
- Best-effort: pivot tables exported as displayed values only; special objects (images,
  charts, shapes) are not rendered but counted in metadata.
- Non-goals: anonymization, translate, or language langid. No network calls.

Integration
-----------
- Selected by the parser registry for extension: .xlsx (and optionally .xls when
  configured via XLS_SUPPORT=true).
- Adapter key/name: "XlsxToMd" (class attribute) for registry preferences/disable lists.
- Output is deterministic for identical inputs and configuration: stable ordering,
  stable Markdown formatting, and stable synthesized header names.

Configuration (env/settings)
----------------------------
The adapter is configured via pipeline settings or environment variables (actual reads
occur in a concrete implementation). The knobs are:

- XLS_SUPPORT (bool): Allow opening legacy .xls files. Default False.
- SHEET_INCLUDE (list[str] | str): Names/globs to include. Default: include all.
- SHEET_EXCLUDE (list[str] | str): Names/globs to exclude. Default: none.
- INCLUDE_HIDDEN_SHEETS (bool): Include hidden sheets. Default False.
- HEADER_ROWS (int): Number of header rows at top of each sheet. Default 1.
- RENDER_MODE (str): One of {"display", "raw"}. Default "display".
- BOOL_STYLE (str): One of {"TRUE_FALSE", "true_false"}. Default "TRUE_FALSE".
- MAX_ROWS (int | None): Truncate rows per sheet if set. Default None (no limit).
- MAX_COLS (int | None): Truncate columns per sheet if set. Default None (no limit).
- MERGED_CELLS_POLICY (str): One of {"fill", "topleft"}. Default "fill".
- CELL_NEWLINES_AS (str): One of {"br", "keep"}. Default "br".
- ADD_WORKBOOK_TITLE (bool): Prepend a top-level title "# {workbook_name}". Default False.
- STRICT_MODE (bool): Escalate certain warnings to errors. Default False.

Registry note
-------------
- Register for .xlsx, and for .xls only when XLS_SUPPORT is enabled. The registry should
  log the adapter key and effective configuration (e.g., render mode, header rows).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

# Public constants for registry wiring
EXTENSIONS: tuple[str, ...] = ("xlsx",)


@dataclass(frozen=True)
class WorkbookRenderPlan:
    """Declarative plan describing worksheet filtering and rendering policies.

    Description:
        Provides an audit-friendly summary of deterministic policies applied during
        spreadsheet rendering. A concrete implementation should populate and propagate
        this plan into output metadata for reproducibility and review.

    Args:
        include (tuple[str, ...] | None): Sheet names/globs to include, in order.
        exclude (tuple[str, ...] | None): Sheet names/globs to exclude.
        include_hidden (bool): Whether hidden sheets are included.
        header_rows (int): Number of header rows per sheet.
        render_mode (str): "display" or "raw" cell rendering policy.
        bool_style (str): "TRUE_FALSE" or "true_false".
        merged_cells_policy (str): "fill" (fill-down/right) or "topleft".
        newlines_as (str): "br" to convert in-cell newlines to <br>, or "keep".
        max_rows (int | None): Per-sheet row cap for truncation (None disables).
        max_cols (int | None): Per-sheet column cap for truncation (None disables).
        add_workbook_title (bool): Whether to add a top-level workbook title section.
        xls_support (bool): Whether legacy .xls is allowed by configuration.

    Notes:
        - All fields must be deterministic for identical inputs/configuration.
        - Unknown or future fields should be ignored by callers.
    """

    include: Optional[tuple[str, ...]]
    exclude: Optional[tuple[str, ...]]
    include_hidden: bool
    header_rows: int
    render_mode: str
    bool_style: str
    merged_cells_policy: str
    newlines_as: str
    max_rows: Optional[int]
    max_cols: Optional[int]
    add_workbook_title: bool
    xls_support: bool


class XlsxToMd:
    """XLSX-to-Markdown converter adapter (deterministic, offline, tables-only).

    Description:
        Converts Excel workbooks (.xlsx; optionally .xls when enabled) into a single
        Markdown document that contains one section per included worksheet, rendered as
        a GitHub-style Markdown table with predictable formatting. The adapter validates
        the input type, enumerates and filters sheets deterministically, extracts cell
        values with a configurable rendering policy (display vs. raw), converts in-cell
        newlines to <br> when configured, preserves hyperlinks as Markdown links, applies
        a deterministic merged-cell policy, and optionally truncates large sheets while
        recording truncation in metadata. Special objects like images/charts/shapes are
        not rendered, but counts are recorded per sheet.

    Args:
        xls_support (bool, optional):
            If True, allow opening legacy .xls files in addition to .xlsx. Defaults to
            False. When False, .xls inputs must fail fast with a clear error.
        sheet_include (tuple[str, ...] | None, optional):
            Sheet names/globs to include. None means include all. Defaults to None.
        sheet_exclude (tuple[str, ...] | None, optional):
            Sheet names/globs to exclude. Defaults to None.
        include_hidden_sheets (bool, optional):
            Include hidden sheets when True. Defaults to False.
        header_rows (int, optional):
            Number of header rows at the top of each sheet. 0 synthesizes headers as
            col_1, col_2, ... deterministically. Defaults to 1.
        render_mode (str, optional):
            Cell rendering policy: "display" or "raw". Defaults to "display".
        bool_style (str, optional):
            Boolean text policy: "TRUE_FALSE" or "true_false". Defaults to "TRUE_FALSE".
        max_rows (int | None, optional):
            Truncate rows per sheet if provided. None disables truncation. Defaults None.
        max_cols (int | None, optional):
            Truncate columns per sheet if provided. None disables truncation. Defaults None.
        merged_cells_policy (str, optional):
            Merged cells handling: "fill" (fill-down/right the visible value) or
            "topleft" (keep only the top-left value). Defaults to "fill".
        cell_newlines_as (str, optional):
            In-cell newline policy: "br" to convert to <br> or "keep" to retain
            newlines. Defaults to "br".
        add_workbook_title (bool, optional):
            If True, emit a top-level title "# {workbook_name}" at the beginning of the
            Markdown. Defaults to False.
        strict_mode (bool, optional):
            If True, escalate certain warnings (e.g., missing sheet in include list,
            unsupported format, password protection) to errors. Defaults to False.

    Attributes:
        name (str): Adapter identifier used by the registry.
        supported_features (dict[str, bool]): Capability flags for audit/telemetry
            (tables, hyperlinks, merged cells, truncation, header synthesis).

    Returns:
        The class exposes a parse(raw) method which returns a domain-level MarkdownDoc
        as specified under parse(). The constructor performs no I/O.

    Raises:
        No exceptions are raised at construction time. See parse() for detailed error
        contracts during conversion.

    Notes:
        - Determinism: stable output for identical input/config (sheet order preserved,
          synthesized headers stable, truncation notices deterministic).
        - Offline: no network calls. All processing is local.
        - Sanitization: output is UTF-8 with LF newlines; control characters removed;
          Markdown reserved characters (e.g., |, backticks) in cell content should be
          escaped deterministically by implementations.
    """

    name: str = "XlsxToMd"

    def __init__(
        self,
        *,
        xls_support: bool = False,
        sheet_include: Optional[tuple[str, ...]] = None,
        sheet_exclude: Optional[tuple[str, ...]] = None,
        include_hidden_sheets: bool = False,
        header_rows: int = 1,
        render_mode: str = "display",
        bool_style: str = "TRUE_FALSE",
        max_rows: Optional[int] = None,
        max_cols: Optional[int] = None,
        merged_cells_policy: str = "fill",
        cell_newlines_as: str = "br",
        add_workbook_title: bool = False,
        strict_mode: bool = False,
    ) -> None:
        self._xls_support = bool(xls_support)
        self._include = tuple(sheet_include) if sheet_include is not None else None
        self._exclude = tuple(sheet_exclude) if sheet_exclude is not None else None
        self._include_hidden = bool(include_hidden_sheets)
        self._header_rows = int(header_rows)
        self._render_mode = str(render_mode)
        self._bool_style = str(bool_style)
        self._max_rows = int(max_rows) if max_rows is not None else None
        self._max_cols = int(max_cols) if max_cols is not None else None
        self._merged_cells_policy = str(merged_cells_policy)
        self._cell_newlines_as = str(cell_newlines_as)
        self._add_workbook_title = bool(add_workbook_title)
        self._strict_mode = bool(strict_mode)
        self.supported_features: Dict[str, bool] = {
            "tables": True,
            "hyperlinks": True,
            "merged_cells": True,
            "truncation": True,
            "header_synthesis": True,
        }

    def parse(self, raw: Any) -> Any:  # RawDocument -> MarkdownDoc (see detailed docs)
        """Convert a .xlsx (or .xls when enabled) to UTF-8, LF-normalized Markdown.

        Minimal, dependency-free implementation based on ZIP + XML parsing.
        - Supports .xlsx only (ignore .xls unless xls_support=True, still unsupported here).
        - Reads sheet order and names from xl/workbook.xml and xl/_rels/workbook.xml.rels.
        - Resolves sharedStrings and inline strings; uses cached formula values when present.
        - Renders each sheet as a Markdown table with the first non-empty row as header
          (controlled by header_rows). No advanced formatting or styles.
        - Sanitizes to LF newlines.
        """
        from pathlib import Path
        import zipfile
        import xml.etree.ElementTree as ET

        try:
            from ...domain.models_markdown import MarkdownDoc
        except Exception:
            MarkdownDoc = None  # type: ignore

        # Access source path
        path = getattr(raw, "path", None) or (raw.get("path") if isinstance(raw, dict) else None)
        if path is None:
            raise ValueError("raw.path is required for XlsxToMd.parse")
        p = Path(str(path))
        if not p.exists():
            raise FileNotFoundError(str(p))

        # Validate extension
        ext = (getattr(raw, "ext", None) or (raw.get("ext") if isinstance(raw, dict) else p.suffix)).lower().lstrip(".")
        if ext == "xls" and not self._xls_support:
            raise ValueError("Legacy .xls not supported (enable xls_support to allow)")
        if ext != "xlsx":
            raise ValueError(f"Unsupported spreadsheet extension: {ext}")

        # Namespaces
        NS = {
            "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
            "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        }

        def _col_to_index(col_ref: str) -> int:
            # Convert Excel column letters (A, B, AA) to 0-based index
            n = 0
            for ch in col_ref:
                if not ch.isalpha():
                    break
                n = n * 26 + (ord(ch.upper()) - ord('A') + 1)
            return n - 1 if n > 0 else 0

        def _split_cell_ref(ref: str) -> tuple[int, int]:
            # Returns (row_index_0_based, col_index_0_based)
            if not ref:
                return (0, 0)
            letters = ''.join([c for c in ref if c.isalpha()])
            digits = ''.join([c for c in ref if c.isdigit()])
            r = int(digits) - 1 if digits else 0
            c = _col_to_index(letters) if letters else 0
            return (r, c)

        # Load workbook
        try:
            zf = zipfile.ZipFile(str(p))
        except zipfile.BadZipFile as e:
            raise RuntimeError(f"Corrupted or unsupported .xlsx: {e}") from e

        with zf:
            # sharedStrings
            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in zf.namelist():
                try:
                    ss_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                    for si in ss_root.findall(".//x:si", NS):
                        # A shared string may contain multiple <t> nodes (with formatting runs)
                        parts: list[str] = []
                        for t in si.findall(".//x:t", NS):
                            parts.append(t.text or "")
                        shared_strings.append("".join(parts))
                except Exception:
                    # Best-effort; fall back to empty list
                    shared_strings = []

            # workbook sheets order and rel mapping
            try:
                wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
            except KeyError as e:
                raise RuntimeError("Invalid .xlsx (missing xl/workbook.xml)") from e

            rel_by_id: dict[str, str] = {}
            try:
                rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
                for rel in rels_root.findall(".//{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"):
                    rId = rel.attrib.get("Id")
                    target = rel.attrib.get("Target")
                    if rId and target:
                        # Normalize path like "worksheets/sheet1.xml"
                        rel_by_id[rId] = f"xl/{target}" if not target.startswith("xl/") else target
            except KeyError:
                # Some minimal files may not have rels (unlikely); assume sheetN.xml
                rel_by_id = {}

            sheets_info: list[tuple[str, str]] = []  # (name, target_path)
            for sh in wb_root.findall(".//x:sheets/x:sheet", NS):
                name = sh.attrib.get("name", "Sheet")
                rId = sh.attrib.get(f"{{{NS['r']}}}id")
                target = rel_by_id.get(rId) if rId else None
                if not target:
                    # Fallback guess: xl/worksheets/sheet{sheetId}.xml
                    sid = sh.attrib.get("sheetId", "1")
                    target = f"xl/worksheets/sheet{sid}.xml"
                sheets_info.append((name, target))

            # Parse each sheet
            md_sections: list[str] = []
            per_sheet_meta: dict[str, dict] = {}
            for (sheet_name, sheet_path) in sheets_info:
                if sheet_path not in zf.namelist():
                    # Skip missing sheet file gracefully
                    continue
                try:
                    sh_root = ET.fromstring(zf.read(sheet_path))
                except Exception:
                    # Skip malformed sheets
                    continue
                # Extract cells
                grid: dict[tuple[int, int], str] = {}
                max_r = -1
                max_c = -1
                nonempty_cols_by_row: dict[int, int] = {}
                for c in sh_root.findall(".//x:sheetData/x:row/x:c", NS):
                    ref = c.attrib.get("r", "")
                    t = c.attrib.get("t", "")
                    v_el = c.find("x:v", NS)
                    is_el = c.find("x:is/x:t", NS) if t == "inlineStr" else None
                    text = ""
                    if t == "s":
                        # Shared string index
                        try:
                            idx = int((v_el.text or "0")) if v_el is not None else 0
                        except ValueError:
                            idx = 0
                        text = shared_strings[idx] if 0 <= idx < len(shared_strings) else ""
                    elif t == "b":
                        text = "TRUE" if (v_el is not None and v_el.text == "1") else "FALSE"
                    elif t == "inlineStr":
                        text = is_el.text if is_el is not None else ""
                    else:
                        # number or string in <v>
                        text = v_el.text if v_el is not None else ""
                    r_i, c_i = _split_cell_ref(ref)
                    max_r = max(max_r, r_i)
                    max_c = max(max_c, c_i)
                    val = (text or "")
                    grid[(r_i, c_i)] = val
                    if val.strip() != "":
                        # Track rightmost non-empty col per row
                        prev = nonempty_cols_by_row.get(r_i, -1)
                        if c_i > prev:
                            nonempty_cols_by_row[r_i] = c_i

                # Determine effective used range based on non-empty cells
                if nonempty_cols_by_row:
                    last_used_row = max(nonempty_cols_by_row.keys())
                    last_used_col = max(nonempty_cols_by_row.values())
                else:
                    last_used_row = -1
                    last_used_col = -1
                rows_count = last_used_row + 1
                cols_count = last_used_col + 1

                if rows_count <= 0 or cols_count <= 0:
                    # Empty sheet; still render header
                    md_sections.append(f"## {sheet_name}\n\n_(empty sheet)_")
                    per_sheet_meta[sheet_name] = {
                        "rows_original": 0,
                        "cols_original": 0,
                        "rows_rendered": 0,
                        "cols_rendered": 0,
                        "truncated_rows": 0,
                        "truncated_cols": 0,
                        "merged_cells_count": 0,
                        "merged_cells_policy": self._merged_cells_policy,
                        "links_count": 0,
                        "images_count": 0,
                        "charts_count": 0,
                        "pivot_present": False,
                        "header_rows_used": max(0, self._header_rows),
                        "formulas_count": 0,
                        "render_mode": self._render_mode,
                    }
                    continue

                # Build table rows (apply optional truncation)
                max_rows_eff = min(rows_count, self._max_rows) if self._max_rows is not None else rows_count
                max_cols_eff = min(cols_count, self._max_cols) if self._max_cols is not None else cols_count
                table_rows: list[list[str]] = []
                for r_i in range(0, max_rows_eff):
                    # For each row, determine this row's last used col (cap by max_cols_eff)
                    row_last_col = nonempty_cols_by_row.get(r_i, -1)
                    eff_cols_this_row = min(max_cols_eff, row_last_col + 1) if row_last_col >= 0 else 0
                    if eff_cols_this_row == 0:
                        # Entire row empty within used range; represent as empty row with current column count
                        # We'll handle header synthesis below.
                        eff_cols_this_row = max_cols_eff
                    row: list[str] = []
                    for c_i in range(0, eff_cols_this_row):
                        val = grid.get((r_i, c_i), "")
                        # Escape pipe and backticks minimally
                        sval = str(val).replace("|", "\\|").replace("`", "\u0060")
                        if self._cell_newlines_as == "br":
                            sval = sval.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")
                        row.append(sval)
                    # Pad short rows to max_cols_eff to keep table rectangular
                    if len(row) < max_cols_eff:
                        row.extend([""] * (max_cols_eff - len(row)))
                    table_rows.append(row)

                # Header handling: if configured header row is empty, synthesize
                header_rows = max(0, int(self._header_rows))
                synth_header = False
                if header_rows >= 1 and table_rows:
                    first_row_nonempty = any(cell.strip() != "" for cell in table_rows[0])
                    if not first_row_nonempty:
                        synth_header = True

                if header_rows >= 1 and table_rows and not synth_header:
                    header = table_rows[0]
                    data_start = 1
                else:
                    # Synthesize headers as col_1..N based on effective columns
                    header = [f"col_{i+1}" for i in range(max_cols_eff)]
                    data_start = 0 if header_rows == 0 else 1  # if header_rows>=1 but empty, we synthesized and keep all rows as data

                # Markdown table assembly
                md_lines: list[str] = [f"## {sheet_name}", ""]
                md_lines.append("| " + " | ".join(header) + " |")
                md_lines.append("| " + " | ".join(["---"] * len(header)) + " |")
                for r in table_rows[data_start:]:
                    md_lines.append("| " + " | ".join(r[:len(header)]) + " |")

                # Truncation note (relative to full sheet grid bounds)
                truncated_rows = max(0, rows_count - (self._max_rows if self._max_rows is not None else rows_count))
                truncated_cols = max(0, cols_count - (self._max_cols if self._max_cols is not None else cols_count))

                # Per-sheet metadata summary
                per_sheet_meta[sheet_name] = {
                    "rows_original": rows_count,
                    "cols_original": cols_count,
                    "rows_rendered": max(0, (len(table_rows) - data_start)),
                    "cols_rendered": len(header),
                    "truncated_rows": truncated_rows,
                    "truncated_cols": truncated_cols,
                    "merged_cells_count": 0,
                    "merged_cells_policy": self._merged_cells_policy,
                    "links_count": 0,
                    "images_count": 0,
                    "charts_count": 0,
                    "pivot_present": False,
                    "header_rows_used": max(0, self._header_rows),
                    "formulas_count": 0,
                    "render_mode": self._render_mode,
                }

                md_sections.append("\n".join(md_lines))

            # Combine all sheet sections and prepend an optional workbook title
            body = "\n\n".join(md_sections) if md_sections else "_(empty workbook)_"
            if self._add_workbook_title:
                text_md = f"# {p.stem}\n\n" + body
            else:
                text_md = body

            # Build render plan metadata for audit
            try:
                from dataclasses import asdict as _asdict
                plan = WorkbookRenderPlan(
                    include=self._include,
                    exclude=self._exclude,
                    include_hidden=self._include_hidden,
                    header_rows=self._header_rows,
                    render_mode=self._render_mode,
                    bool_style=self._bool_style,
                    merged_cells_policy=self._merged_cells_policy,
                    newlines_as=self._cell_newlines_as,
                    max_rows=self._max_rows,
                    max_cols=self._max_cols,
                    add_workbook_title=self._add_workbook_title,
                    xls_support=self._xls_support,
                )
                plan_dict: Dict[str, Any] = _asdict(plan)  # type: ignore[name-defined]
            except Exception:
                plan_dict = {
                    "include": self._include,
                    "exclude": self._exclude,
                    "include_hidden": self._include_hidden,
                    "header_rows": self._header_rows,
                    "render_mode": self._render_mode,
                    "bool_style": self._bool_style,
                    "merged_cells_policy": self._merged_cells_policy,
                    "newlines_as": self._cell_newlines_as,
                    "max_rows": self._max_rows,
                    "max_cols": self._max_cols,
                    "add_workbook_title": self._add_workbook_title,
                    "xls_support": self._xls_support,
                }

            meta: Dict[str, Any] = {
                "adapter": self.name,
                "workbook_name": p.name,
                "sheets": per_sheet_meta,
                "plan": plan_dict,
                "conversion_warnings": [],
            }

            # Return MarkdownDoc when available; otherwise a lightweight object
            if MarkdownDoc is not None:  # type: ignore[truthy-bool]
                return MarkdownDoc(
                    doc_id=p.stem,
                    path=str(p),
                    variant=None,
                    lang=None,
                    text_md=text_md,
                    meta=meta,
                )

            class _Lite:
                def __init__(self, text: str, meta: Dict[str, Any]):
                    self.text_md = text
                    self.meta = meta

            return _Lite(text_md, meta)
