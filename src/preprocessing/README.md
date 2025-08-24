# Habithon Preprocessing — Folder → Markdown (convert-only)

This package converts documents in a folder to Markdown files (.md), preserving the
directory structure. It’s intentionally convert-only: no LLMs, anonymization,
vector stores, or full pipelines. Outputs are UTF-8 with LF newlines and optional
metadata sidecars.

Supported formats (offline):
- DOCX (Word)
- XLSX (Excel)
- PDF (text extraction via pdfminer.six)
- MSG (Outlook .msg)

Note: Image OCR (JPG/JPEG) isn’t enabled by default in the CLI. The JPG adapter is
present as a stub and not included in defaults. Use PDF with OCR externally if needed.

## Install

From the monorepo root (or src/preprocessing):

```bash
# Base (no heavy parser deps)
pip install ./src/preprocessing

# With document parsers (recommended)
pip install './src/preprocessing[parsers]'
```

Extras installed by [parsers]:
- pdfminer.six (PDF)
- python-docx (DOCX)
- extract_msg (MSG)

## CLI — mdify

Convert a folder to Markdown while preserving the relative structure:

```bash
# Minimal
activate-your-venv-if-needed
mdify --src ./in --out ./out

# Common options
mdify --src ./in --out ./out \
  --include-ext .pdf .docx .xlsx .msg \
  --exclude-glob '**/~$*' '**/tmp/**' \
  --skip-existing \
  --workers 4 \
  --report ./out/run.json \
  --log-file ./out/cli_run.log

# Plan only (no writes)
mdify --src ./in --out ./out --dry-run --progress plain
```

Behavior:
- Mapping: src_dir/a/b/file.docx → out_dir/a/b/file.md
- Assets: If an adapter exports assets, they’re placed under out_dir/.../assets/ by default
- Metadata: --write-meta sidecar|inline|none (default sidecar writes file.md.meta.json)
- Newlines: normalized to LF by default (--normalize-eol lf|keep)
- Defaults: include extensions .docx .xlsx .pdf .msg (case-insensitive); JPG/JPEG are not processed by default
- Selection: use --exclude-glob to skip paths under --src; cap with --max-files
- Logging: --log-level ERROR|WARNING|INFO|DEBUG; optional --log-file (default outputs/logs/cli_run.log)
- Progress: --progress auto|plain|none (auto degrades to plain)

Run `mdify --help` for all options (scanning filters, overwrite policy, progress UI, etc.).

Exit codes:
- 0 → all selected files converted successfully
- 1 → completed with some errors (at least one file failed)
- 2 → argument/usage error (invalid paths/options)
- 3 → fatal initialization error (app layer unavailable or crashed)

## Python API (lightweight)

```python
from pathlib import Path
from preprocessing.app.convert_only import plan_folder, convert_folder

cfg = {
    "scan": {
        "recurse": True,
        "include_ext": [".pdf", ".docx", ".xlsx", ".msg"],
        "exclude_glob": [],
    },
    "write": {
        "overwrite": False,
        "assets_subdir": "assets",
        "write_meta": "sidecar",
        "normalize_eol": "lf",
    },
    "runtime": {"workers": 2, "on_error": "skip", "dry_run": False, "strict": False},
    "ui": {"log_level": "INFO", "progress": "plain"},
    "report": "./out/run.json",
}

# Compute a deterministic plan without writing
plan = plan_folder(Path("./in"), Path("./out"), cfg)

# Execute conversion
run = convert_folder(Path("./in"), Path("./out"), cfg)
print(run.converted_ok, "converted,", run.failed, "failed")
```

## Supported formats and dependencies

- DOCX → Markdown: requires python-docx
- XLSX → Markdown: no extra runtime deps (pure Python ZIP/XML)
- PDF → Markdown: requires pdfminer.six
- MSG → Markdown: requires extract_msg

Install them via the `[parsers]` extra as shown above.

## Determinism and mapping

- Relative structure from --src is preserved under --out.
- Output text is UTF-8 with LF by default; control chars removed (TAB/LF kept).
- Per-file assets (when any) go to a sibling assets/ directory by default.

## Troubleshooting

- “No parser for extension …”: ensure you installed the `[parsers]` extra.
- PDF extraction errors: verify `pdfminer.six` is installed in the same environment.
- DOCX/MSG imports failing: check that `python-docx`/`extract_msg` are installed.
- Newlines look odd on Windows: outputs are LF by default; use `--normalize-eol keep` if needed.

## License

MIT
