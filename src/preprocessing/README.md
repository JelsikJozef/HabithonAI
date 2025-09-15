# Habithon Preprocessing — Convert ➜ English Markdown (offline)

This module turns mixed-source documents into clean Markdown and can produce a deterministic, offline English variant that preserves Markdown structure. It’s designed for batch use, reproducibility, and no network calls at runtime.

- Stage 1: convert — Parse supported inputs and write Markdown (.md) plus assets.
- Stage 2: translate — Create an English variant from Markdown using an offline MT engine. Layout, code blocks, links, image targets, and other non-text structures are preserved.

Both stages are orchestrated by a single CLI and can be run as convert-only, convert+translate, or translate-only.


## Supported workflow

- convert: folder of source files ➜ folder of Markdown, mirroring the directory structure
  - Formats (offline): DOCX, XLSX, PDF (pdfminer.six), MSG
  - Assets (images, etc.) are exported next to Markdown under an assets/ subdir
- translate: Markdown ➜ English Markdown, structure-preserving
  - Language detection via fastText lid.176.bin
  - Engines: ct2_nllb (CTranslate2 + NLLB) and marian_opus (Transformers MarianMT)
  - Optional glossary injection and translation caching

Use cases:
- Convert only: produce normalized Markdown to feed downstream tools.
- Convert + Translate: produce original Markdown and an English variant in one run.
- Translate-only: take existing Markdown trees and add an English variant.


## English variant translation

Why: English unification up-front simplifies later anonymization, enrichment, and retrieval. The translation step is deterministic and offline for reproducibility.

How it works:
- Language detection: fastText model lid.176.bin (local file). Candidates can be constrained for better routing.
- Engines (offline only):
  - ct2_nllb: CTranslate2 runtime with NLLB model; SentencePiece tokenizer.
  - marian_opus: Hugging Face Transformers Marian models (CPU by default). Must exist in local cache or as local paths.
- Markdown segmenter: Extracts only human text nodes; preserves code fences/inline code, link and image destinations, headings, tables, and spacing. Options let you translate link labels, alt text, and table cells, and control soft-break collapsing.
- Glossary (optional): Apply pre/post/both substitutions on plain text segments.
- Cache (optional): Per-segment translation cache keyed by a deterministic engine fingerprint plus segment text/context.

Outputs:
- The English variant is written under an en/ subfolder within the output tree, with variant metadata preserved.


## CLI — mdify

One command drives both phases.

Basic usage (convert only):

```bash
mdify --src ./in --out ./out \
  --include-ext .pdf .docx .xlsx .msg \
  --skip-existing --workers 4 --report ./out/run.json
```

Convert + translate to English (offline):

```bash
mdify --src ./in --out ./out \
  --make-english \
  --translator ct2_nllb \
  --workers 4 --report ./out/run.json
```

Translate-only on existing Markdown (no convert phase):

```bash
# Translate .md found under --src, write English variant under --out/en
mdify --src ./out --out ./out \
  --translate-only --make-english \
  --translator marian_opus --workers 2
```

Improve routing on mixed-language corpora (new LangID tuning):

```bash
# Constrain candidates and widen the detector window
mdify --src ./in --out ./out \
  --make-english --translator ct2_nllb \
  --lang-candidates sk,de,cs,en --lang-max-chars 20000 --lang-min-chars 80
```

Common flags:
- Scanning & selection (convert):
  - --recurse | --no-recurse
  - --include-ext .pdf .docx .xlsx .msg
  - --exclude-glob PATTERN ...
  - --max-files N
- Output & writing:
  - --overwrite | --skip-existing (default)
  - --assets-subdir assets (default)
  - --write-meta none|sidecar|inline (default sidecar writes file.md.meta.json)
- Performance & reliability:
  - --workers N
  - --on-error skip|fail
  - --dry-run (plan only; no writes)
- Translation (English variant):
  - --make-english
  - --translator ct2_nllb|marian_opus
  - --translate-only (skip convert; scan Markdown under --src)
  - --tgt-lang en (default)
  - --lang-candidates sk,de,cs,pl,hu,en
  - --lang-max-chars N   # new: widen detector text window
  - --lang-min-chars N   # new: minimum length before trusting scores
  - --segment-max-chars N
  - --translate-link-label true|false
  - --translate-alt-text true|false
  - --translate-table-cells true|false
  - --collapse-softbreaks true|false
  - --glossary-id ID
  - --glossary-mode pre|post|both|none
  - --mt-cache PATH (override cache root)
  - --cache-disabled
  - --translate-on-error skip|fail_fast
- Logging & diagnostics:
  - --log-level ERROR|WARNING|INFO|DEBUG
  - --progress auto|plain|none
  - --report PATH (writes a structured JSON run report)
  - --log-file PATH (defaults to ./outputs/logs/cli_run.log)
- Compatibility:
  - --normalize-eol lf|keep (default lf)
  - --strict (treat some warnings as errors)

Exit codes:
- 0: all selected files converted (and translation, if requested) succeeded or were skipped appropriately
- 1: completed with some errors
- 2: usage/config error
- 3: fatal initialization error


## Settings: settings_translation.py

This module holds the translation configuration and validation helpers. Key sections:
- engine: ct2_nllb or marian_opus
- ct2_nllb: model_dir, compute_type, device, num_threads, src_lang_map, tgt_lang_code
- marian: models mapping (src_lang ➜ model id/path), device, dtype, local_files_only, hf_cache_dir
- decoding: deterministic parameters for each engine (beams, penalties, batch sizes)
- segmenter: segment_max_chars, translate_alt_text, translate_link_label, translate_table_cells, preserve_whitespace, collapse_softbreaks, language_hint
- langid: impl fasttext, model_path (lid.176.bin), max_chars, min_chars, candidates
- glossary: enabled, mode (pre|post|both|none), glossary_id, db_path, regex_enabled
- cache: enabled, backend sqlite|fs, root_path, limits, namespace
- io: overwrite, dry_run, workers, on_error (skip|fail_fast)
- policy: network_access=false, allow_online_model_download=false (offline guarantee)
- logging/telemetry/schema: light diagnostics and fingerprints

Helpers:
- validate_translation_settings(cfg) ➜ (ok, issues)
- capabilities_summary(cfg) ➜ compact string for logs

Environment overrides (examples):
- HABITHON_TRANSLATION_ENGINE=ct2_nllb|marian_opus
- HABITHON_CT2_MODEL_DIR=/abs/path/to/ct2-nllb-model
- HABITHON_LANGID_MODEL_PATH=/abs/path/to/lid.176.bin
- HABITHON_LANGID_MAX_CHARS=20000   # new
- HABITHON_LANGID_MIN_CHARS=80      # new
- HABITHON_MT_CACHE_PATH=/abs/path/to/mt_cache.sqlite
- HABITHON_IO_WORKERS=4, HABITHON_IO_OVERWRITE=1


## Output layout and English variant

- Converted Markdown mirrors the relative structure from --src under --out.
- English variant is written under: --out/en/<relative-path>.md
- Assets for the English file are placed under the sibling assets/ folder (same name configured by --assets-subdir).
- When --write-meta=sidecar, a sidecar JSON file file.md.meta.json is written next to the English Markdown.


## Offline guarantee

The translation step is designed to run entirely offline:
- No network calls at runtime; model files must be present locally.
- ct2_nllb: provide a CTranslate2-converted NLLB model directory and SentencePiece model.
- marian_opus: ensure the specified Marian model ids/paths resolve locally (HF cache dir may be pointed to via HABITHON_MARIAN_CACHE_DIR). Set local_files_only=True (default).
- fastText: lid.176.bin must be on disk; path configurable via settings or env.
- The cache uses local SQLite or filesystem paths only.

Before running, pre-download models and set model paths in settings_translation.py or via environment variables.


## Examples

Basic conversion:

```bash
mdify --src ./resources/samples --out ./outputs/md --workers 2 --skip-existing
```

Conversion + translation to English (ct2_nllb):

```bash
mdify --src ./resources/samples --out ./outputs/md \
  --make-english --translator ct2_nllb \
  --workers 4 --report ./outputs/cli_report.json
```

Translate-only on pre-existing Markdown (marian_opus):

```bash
mdify --src ./outputs/md --out ./outputs/md \
  --translate-only --make-english \
  --translator marian_opus --workers 2 --overwrite
```


## Install

From the monorepo root (or src/preprocessing):

```bash
# Base (convert-only)
pip install ./src/preprocessing

# With document parsers (recommended for convert)
pip install './src/preprocessing[parsers]'

# Add translation engines and langid (offline)
# CTranslate2 + SentencePiece + fastText
pip install './src/preprocessing[ct2,langid]'

# Marian (Transformers + torch) + fastText
pip install './src/preprocessing[marian,langid]'

# Everything (parsers + both engines + langid)
pip install './src/preprocessing[all]'
```


## Architecture (brief)

- presentation: CLI and API boundary (argument parsing, reporting)
- app: orchestration for convert and ensure_english flows
- adapters: concrete implementations (parsers, langid, translate, caching, segmenter)
- domain: pure data models, ports (interfaces), and error types

This layering keeps the CLI import-light and enables deterministic, testable behavior.


## Dependencies (by capability)

- Convert: pdfminer.six (PDF), python-docx (DOCX), extract_msg (MSG)
- LangID: fasttext (uses lid.176.bin)
- Translate (ct2_nllb): ctranslate2, sentencepiece
- Translate (marian_opus): transformers, torch


## Troubleshooting

- “No parser for extension …”: install the [parsers] extra.
- Translation settings invalid: run with --make-english and inspect logs; ensure lid.176.bin, model paths, and cache parent directories exist. validate_translation_settings(...) in settings_translation.py can help.
- Marian offline: pre-populate the HF cache (no network at runtime) and set HABITHON_MARIAN_CACHE_DIR if needed.
- Newlines: outputs default to LF; switch via --normalize-eol keep.


## License

MIT


## Advanced routing (model-only, no heuristics)

Purpose: choose the correct source language and verify that non-English inputs are actually translated into English. Uses only model scores and deterministic thresholds.

Components:
- Domain ports:
  - LanguageDetectTopKPort.detect_topk(text, k, hints) ➜ {lang_code, confidence, topk[], flags}
  - EnglishDetectPort.english_confidence(text) ➜ float [0,1]
  - SimilarityPort.similarity(a, b) ➜ float [0,1] (1 == identical)
- Adapters:
  - fastText LangID top-k (adapters/langid/fasttext_langid.py)
  - fastText English detector (adapters/langid/english_detector_fasttext.py)
  - Trigram Jaccard similarity (adapters/similarity/simple_similarity.py)

Policy (app layer: app/ensure_english.py):
- Document-level LangID via top-k. If confidence < τ_low or top-1 vs top-2 margin < δ_close, run a pre-translation probe on a small slice using a few top-k candidates. Select the source that maximizes English-confidence minus a small similarity penalty.
- Translate with the selected source. Validate output: English-confidence ≥ τ_en and similarity(input, output) < similarity_noop_threshold (not near-identity) when source ≠ en.
- Retry ladder (bounded): primary engine with selected source, then other top-k sources, then optional secondary engine with the same order, up to max_retries.
- Telemetry: per-document meta.routing includes topk, flags, selected_src, probe, attempts, and post-check metrics (en_confidence, similarity) for audit.

Configuration (settings_translation.TRANSLATION["routing"]):
- tau_low: float, LangID low-confidence threshold (default 0.70)
- delta_close: float, top-1 vs top-2 closeness margin (default 0.05)
- tau_en: float, English confidence threshold for outputs (default 0.90)
- similarity_noop_threshold: float, identity detection (default 0.92)
- probe: { k: int candidates (default 3), slice_chars: int sample size (default 600) }
- max_retries: int, cap on attempts across engines/sources (default 3)
- candidates: list[str], candidate source codes limit

Minimal wiring (optional, for full routing):
- Provide ports.english_detector and ports.similarity to ensure_english.* functions; otherwise, the flow runs without probe/post-checks but remains backwards-compatible.

Environment overrides:
- HABITHON_ROUTING_TAU_LOW, HABITHON_ROUTING_DELTA_CLOSE, HABITHON_ROUTING_TAU_EN,
  HABITHON_ROUTING_SIM_NOOP, HABITHON_ROUTING_PROBE_K, HABITHON_ROUTING_PROBE_SLICE_CHARS,
  HABITHON_ROUTING_MAX_RETRIES
