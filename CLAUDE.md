# CLAUDE.md

> Project guidance for Claude Code. These are always-true rules for this repository.
> Verify the paths and commands below against the real repo and adjust where they differ
> from your actual setup before relying on them.

## What this project is

A system for **anonymized document processing and AI-assisted search**. Documents are
converted to a common text format, language-detected and optionally translated, scanned for
personal data (PII), deterministically anonymized, segmented, enriched with LLM-generated
metadata, and prepared for vector + metadata retrieval.

- **Language / runtime:** Python 3.12
- **Layout:** monorepo, source under `src/`, split into bounded contexts.
- **Bounded contexts:** `preprocessing`, `anonymization`, `gui`, `shared`.

## Architecture rules (do not violate)

Each bounded context follows **hexagonal architecture**. Keep these layers separate:

- `domain/` — domain entities and **ports** (interfaces). Depends on nothing but stdlib/typing.
- `adapters/` — concrete implementations of ports over third-party libraries.
- `app/` — application use cases that orchestrate domain + adapters.
- `presentation/` — entry points (CLI / API / GUI views).

Hard rules:

- **Domain and use cases depend only on ports, never on concrete adapters.** Presidio,
  storage, crypto, translation engines, etc. are adapters that implement ports.
- A concrete technology must always be swappable behind its port without touching the domain.
- Do not introduce cross-context imports of internal layers; communicate through `app`/ports.

## Critical invariants (security & correctness — never break these)

1. **Anonymize before any LLM/API call.** The OpenAI LLM (metadata generation /
   summarization) must only ever receive the **anonymized English** text. Original or
   sensitive text must never leave the local environment. If you ever find code sending
   non-anonymized content to an external API, that is a bug — stop and flag it.
2. **Offline-first.** Language detection, translation, and anonymization models load from
   **local storage** and must not require network access at runtime. The *only* permitted
   network call is the optional LLM metadata step via the OpenAI API.
3. **Deterministic processing.** Same input + same config must produce the same output.
   - Translation: deterministic decoding, fixed beam search, **no random sampling**.
   - LLM calls: older models use `temperature=0`, `top_p=1`, fixed `seed`; newer models omit
     sampling params per their constraints.
4. **Deterministic identifiers.** Canonicalize text (Unicode **NFKC**, normalized line
   endings, trimmed whitespace), then derive **SHA-256** content hashes. Document ID, context
   ID (binds the Token Vault to a document variant), and chunk/point IDs are derived from
   canonical content so that original, translated, anonymized versions and chunks stay linked.
5. **Token Vault isolation.** Mappings between original and replaced values live in a
   separate store (file/JSON, optionally PostgreSQL). Keep them strictly separated from other
   outputs for security and auditability.
6. **UTF-8 everywhere.** Normalize encoding to UTF-8 during conversion.
7. **Markdown is the intermediate format.** Conversion targets Markdown. Translation must
   **preserve Markdown structure** — code blocks, inline code, link targets, image URLs and
   table layout stay untouched; only human-readable text is translated and put back in place.
8. **Retrieval is not naive RAG.** Ranking combines vector similarity with metadata
   (keyword) matching that boosts topically relevant documents. Do not collapse it to a plain
   nearest-neighbor lookup.

## Domain specifics to respect

**Anonymization context**
- Entities: `PiiEntity` (type, position, value, confidence score), `TokenMapping`.
- Ports: `DetectorPort`, `TokenVaultPort`, `Crypto`.
- `PresidioDetector` adapter builds the `AnalyzerEngine` + recognizer registry.
- Model resolution order per language: requested model → configured fallback → multilingual
  `xx_ent_wiki_sm` → if none available, deactivate that language.
- A `language router` prefers the requested language if supported, otherwise the fallback.
- Regex fallback recognition exists for when Presidio is unavailable — keep it working.
- Behavior is configured centrally via `PiiSettings` (model-per-language, person score,
  context words/titles for false-positive reduction, e.g. SK "pán/pani/Ing./Mgr.",
  DE "Herr/Frau/Dr.").
- Entity merging produces a **non-overlapping** set: priority by type (person > organization),
  then longer span, then higher score; followed by whitespace/boundary post-processing.

**Translation (in `preprocessing`)**
- `FastTextLangId` adapter over `lid.176.bin`; detection runs over Markdown after stripping
  noise (code, inline code, URLs, HTML). Returns language code + calibrated confidence.
- Target language is **English**; skip translation if the document is already English.
- `TranslatePort` with two interchangeable adapters: CTranslate2 + NLLB-200, and
  Marian/OPUS-MT (Helsinki-NLP) via Transformers. Local-files-only mode.
- Optional glossary and per-segment translation cache.

**Segmentation**
- Structure-aware over Markdown boundaries (headings, paragraphs, lists, tables) — **not**
  fixed character splitting.
- Defaults (`Step2Config`): target 1500 chars, hard max 2500, min 400, overlap 200.
- Record the segmentation policy version for auditability.

**LLM summarization / metadata**
- Input is the **anonymized English** text (see invariant 1).
- Output is strict JSON: one-sentence summary (≤ 30 words) + **exactly 5** keywords,
  normalized (lowercase, no diacritics, deduplicated) and sorted by importance.
- Retry on rate-limit / server errors with exponential backoff.
- Record the summarization policy version with the artifact.

## Storage layout (keep consistent)

- English inputs in `en/`; anonymized outputs in `hashed_documents/` with an `_anon` suffix.
- Derived artifacts under `outputs/artifacts/` keyed by document ID, split into
  `step1`–`step3`; diagnostics stored separately.
- Per-document sidecars: token-mapping file, metadata file, and JSON-Lines records for the
  search layer (document ID, metadata, control preview).
- Glossary in SQLite; on-disk translation cache.
- Vector store (Qdrant) infrastructure runs via Docker Compose; populating/searching it
  belongs to the (separate) retrieval layer.

## Commands (adjust to the real repo)

- Run pipeline (preprocessing CLI): `mdify ...`
- Anonymization module has its own CLI; GUI entry point is `gui.app`.
- Tests: `pytest`
- Lint / format: `ruff check .` and `black .`
- Type check: `mypy`
- Pre-commit hooks: `pre-commit run --all-files`
- Services (Qdrant, Presidio): `docker compose up`

## How I want you to work

- Work on **one bounded context (or one pipeline step) at a time**; don't refactor across
  contexts in a single change.
- Before treating a task as done: run `pytest`, `ruff`, and `mypy`, and make them pass.
- Prefer adding/extending tests for any behavior you change.
- When a change touches an invariant above, call it out explicitly and confirm before
  proceeding — especially anything involving sending text to the OpenAI API.
- Match the existing hexagonal layering; put new third-party usage behind a port + adapter,
  not directly in `domain`/`app`.
- Keep outputs deterministic; don't introduce randomness, wall-clock time, or hidden network
  calls into the pipeline.
- Ask before deleting files, changing storage layout, or altering identifier derivation.
