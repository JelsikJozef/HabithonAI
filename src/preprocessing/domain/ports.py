from __future__ import annotations

"""Stable interfaces (ports) for the preprocessing application layer.

Ports define framework-agnostic contracts that adapters must implement. They are
pure in the sense that calling methods shouldn't cause side effects beyond their
scope unless explicitly stated (e.g., writers or DB ports). Implementations must
be offline by default, deterministic for the same input, and must not leak
vendor/library exceptions—map them to domain errors instead.

General rules:
- All arguments and return values must be JSON-serializable or domain models.
- Methods that perform writes must document atomicity and idempotency policies.
- "Raises" sections list domain errors only.

Contracts (applies to all ports in this module):
- Text encoding: All text passed in/out is UTF-8 encodable with LF ("\n") newlines only.
- Language codes: Lowercase, two-letter where possible (e.g., "sk", "de", "cs", "pl", "hu", "en").
  Adapters may map extended tags internally, but the port contract stays simple.
- Immutability: Input domain models (e.g., MarkdownDoc) must not be mutated; return new objects.
- Determinism: Identical inputs and configuration must yield identical outputs (including whitespace).
- Structure preservation (TranslatePort): Markdown structure (headings, lists, tables, code/link/image tokens)
  must be preserved; only text nodes are translated.
- Telemetry context: Methods may accept optional "context" dicts for logging/telemetry. Implementations
  may ignore them but must never fail because a context is provided.
- Concurrency: Read-only operations should be thread-safe; adapters may batch internally within documented limits.
"""

from collections.abc import AsyncIterator, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, TypedDict, runtime_checkable

from .errors import DomainError
from .models import ParsedDocument, RawDocument
from .models_markdown import MarkdownDoc

# ----------------------------
# Existing ports (preserved)
# ----------------------------


class IngestionPort(Protocol):
    """Source of documents.

    - ingest_batch: find all files matching globs under root and return RawDocument iterator
    - ingest_watch: stream newly created/modified files (watch mode)
    """

    def ingest_batch(self, root: Path, globs: tuple[str, ...]) -> Iterable[RawDocument]:
        """Discover and yield raw documents under a root directory.

        Args:
            root: Absolute root path to search.
            globs: File patterns to include (e.g., ("**/*.pdf", "**/*.docx")).

        Returns:
            Iterable[RawDocument]: Finite iterable of discovered inputs.
        """
        ...

    async def ingest_watch(self, root: Path, globs: tuple[str, ...]) -> AsyncIterator[RawDocument]:
        """Asynchronously yield documents as they are created or modified.

        Args:
            root: Absolute root path to watch.
            globs: File patterns to include when watching.

        Returns:
            AsyncIterator[RawDocument]: Async stream of new/changed files.
        """
        ...


class ParserRegistryPort(Protocol):
    """Select a concrete parser by extension/mime."""

    def get(self, ext: str) -> Any:
        """Return the highest-priority parser adapter for an extension.

        Args:
            ext: File extension; case-insensitive; leading dot allowed.

        Returns:
            Any: Parser adapter instance.

        Raises:
            ParserNotFoundError: When no parser is registered for the extension.
        """
        ...

    def supported(self) -> set[str]:
        """Return the set of supported extensions (lowercase, no dot).

        Returns:
            set[str]: Supported extensions.
        """
        ...


class OcrPort(Protocol):
    """OCR for images/PDF with low text content.

    Backward-compatible surface kept:
    - run(): legacy method returning plain text.

    Forward-compatible addition:
    - recognize(): structured result with confidences.
    Implementers may raise NotConfigured for the new method.
    """

    def run(self, input_path: Path, *, languages: tuple[str, ...]) -> str:
        """Perform OCR and return plain text output.

        Args:
            input_path: Absolute path to the image or PDF file.
            languages: Tuple of language codes for OCR (e.g., ("eng",)).

        Returns:
            str: Recognized text.

        Raises:
            OcrError: When engine is unavailable or recognition fails.
        """
        ...

    def recognize(self, image_path: str, *, langs: list[str]) -> OcrResult:  # type: ignore[override]
        """Perform OCR and return structured result with confidences.

        Args:
            image_path: Absolute path to an image file.
            langs: List of language codes.

        Returns:
            OcrResult: Structured OCR result with confidences per block.

        Raises:
            OcrError: When engine is unavailable or recognition fails.
        """
        ...


class EnrichmentPort(Protocol):
    """Heuristics and metadata enrichment.

    Backward-compatible surface kept:
        - enrich(doc): returns an updated ParsedDocument.

    Forward-compatible addition:
        - enrich(text, schema): structured JSON enrichment for anonymized English.
          Implementers may raise NotConfigured if unused.
    """

    def enrich(self, doc: ParsedDocument) -> ParsedDocument:
        """Enrich a parsed document with additional heuristics/metadata.

        Args:
            doc: Parsed document to enrich.

        Returns:
            ParsedDocument: The enriched document.
        """
        ...

    def enrich_text(self, text: str, *, schema: str = "summary_tags_v1") -> dict:
        """Enrich anonymized English text using a strict JSON schema.

        Args:
            text: Anonymized English text to enrich.
            schema: Output schema identifier. Defaults to "summary_tags_v1".

        Returns:
            dict: JSON-serializable object following the requested schema.

        Raises:
            EnrichmentError: On non-JSON response or schema mismatch.
        """
        ...


class LlmEnrichmentPort(Protocol):
    """Semantic enrichment (summary, keywords)."""

    def summarize(self, text: str) -> str:
        """Return a concise extractive/abstractive summary for text.

        Args:
            text: Input text to summarize.

        Returns:
            str: Summary text.
        """
        ...

    def keywords(self, text: str, top_k: int = 10) -> list[str]:
        """Extract top-K keywords from text.

        Args:
            text: Input text.
            top_k: Number of keywords to return. Defaults to 10.

        Returns:
            list[str]: Keywords ordered by relevance.
        """
        ...


# ----------------------------
# New ports for Prompt 3
# ----------------------------


class Metadata(TypedDict):
    summary: str
    tags: list[str]


class MetadataGenerator(Protocol):
    """Generate structured metadata for anonymized text."""

    def generate(self, document_text: str) -> Metadata:
        """Return metadata with summary and tags.

        Deterministic for identical input. Implementations must not leak vendor exceptions.
        """
        ...


class DeAnonymizer(Protocol):
    """Restore original values for hashed/tokenized placeholders in text."""

    def restore(self, anonymized_text: str, *, context_id: str) -> str:
        """Return de-anonymized text by using stored mappings for the given context_id."""
        ...


class DedupPort(Protocol):
    """Document-level duplicates langid."""

    def exists(self, content_hash: str) -> bool:
        """Check if a given content hash is already known.

        Args:
            content_hash: Deterministic content hash string.

        Returns:
            bool: True if previously seen; False otherwise.
        """
        ...

    def remember(self, content_hash: str) -> None:
        """Record a content hash to prevent duplicates.

        Args:
            content_hash: Deterministic content hash string.
        """
        ...


class QualityPort(Protocol):
    """Quality checks for text/data."""

    def evaluate(self, doc: ParsedDocument) -> dict[str, Any]:
        """Compute quality metrics for a parsed document.

        Args:
            doc: Parsed document to evaluate.

        Returns:
            dict[str, Any]: JSON-serializable metrics.
        """
        ...


class SerializerPort(Protocol):
    """Output records writer."""

    def append(self, record: dict[str, Any]) -> None:
        """Append a JSON-serializable record to the output sink.

        Args:
            record: Record to write.

        Raises:
            WriteError: On serialization or I/O failures.
        """
        ...

    def close(self) -> None:
        """Flush and close the underlying sink.

        Raises:
            WriteError: On finalization failures.
        """
        ...


# ----------------------------
# Convert-only ports (required)
# ----------------------------


@runtime_checkable
class FileRef(Protocol):
    """Minimal file reference used by converters.

    Description:
        Protocol satisfied by :class:`RawDocument` and other compatible objects.

    Attributes:
        path: Absolute file path.
        ext: Lowercase extension without leading dot.
        size: Size in bytes (>= 0).
        mtime: Modification timestamp.
    """

    path: Path
    ext: str
    size: int
    mtime: datetime


class MarkdownConverterPort(Protocol):
    """Convert a file (DOCX/XLSX/PDF/JPG/MSG…) into a :class:`MarkdownDoc`.

    Behavior:
        - Pure and offline by default. Must not write to disk (except ephemeral temp files).
        - Deterministic for the same input and settings.

    Raises:
        UnsupportedFormatError, ParserDependencyMissingError, CorruptedFileError,
        PasswordProtectedFileError, and other domain errors as appropriate.
    """

    def convert(self, file_ref: FileRef, *, settings: Any | None = None) -> MarkdownDoc:
        """Convert an input file to a MarkdownDoc.

        Args:
            file_ref: File reference with path, ext, size, and mtime.
            settings: Optional adapter-specific settings object.

        Returns:
            MarkdownDoc: Converted document (pre-normalization).

        Raises:
            UnsupportedFormatError: When the input format is not supported.
            ParserDependencyMissingError: When a required dependency is missing.
            CorruptedFileError: When the file is unreadable or broken.
            PasswordProtectedFileError: When a password is required.
        """
        ...


class EncodingNormalizerPort(Protocol):
    """Enforce UTF-8, LF, and Unicode canonicalization for Markdown.

    Behavior:
        - Must not break Markdown structures (e.g., fenced code blocks).
        - Returns new :class:`MarkdownDoc` instances; do not mutate inputs.

    Raises:
        NormalizationError (size limit exceeded, invalid options, irrecoverable anomalies).
    """

    def normalize_doc(self, doc: MarkdownDoc, *, options: Any | None = None) -> MarkdownDoc:
        """Normalize a MarkdownDoc and return a new instance.

        Args:
            doc: Input document to normalize.
            options: Optional normalization options.

        Returns:
            MarkdownDoc: Normalized document with encoding set to "utf-8".

        Raises:
            NormalizationError: On invalid options or irrecoverable anomalies.
        """
        ...

    def normalize_text(self, text: str, *, options: Any | None = None) -> tuple[str, dict]:
        """Normalize raw Markdown text.

        Args:
            text: Raw Markdown text to normalize.
            options: Optional normalization options.

        Returns:
            tuple[str, dict]: Normalized text and a JSON-serializable report.

        Raises:
            NormalizationError: On invalid options or size limit exceeded.
        """
        ...


# Writer context and results (minimal contracts)
class TargetPaths(TypedDict):
    """Deterministic target paths for a document write operation.

    Keys:
        out_md_path: Absolute path of the Markdown file to write.
        assets_dir: Absolute path of the assets directory for this document.
        sidecar_meta_path: Absolute path of the sidecar metadata file or None.
    """

    out_md_path: str
    assets_dir: str
    sidecar_meta_path: str | None


@runtime_checkable
class WriterContext(Protocol):
    """Immutable context controlling write behavior and path mapping.

    Attributes:
        out_root: Absolute output root directory.
        src_root: Absolute source root used to compute relative structure.
        assets_subdir: Subdirectory name used for assets (e.g., "assets").
        assets_layout: "per_doc" or "flat".
        write_meta: "none", "sidecar", or "inline".
        overwrite: Whether to overwrite existing outputs.
        dry_run: If True, perform no writes.
        ensure_final_newline: Optional enforcement of terminal LF policy.
    """

    out_root: str
    src_root: str
    assets_subdir: str
    assets_layout: str
    write_meta: str
    overwrite: bool
    dry_run: bool
    ensure_final_newline: bool | None


class WriteResult(TypedDict):
    """JSON-serializable result of a write operation.

    Keys:
        status: One of {"ok", "skip_existing", "dry_run"}.
        out_md_path: Absolute path of the Markdown file.
        assets_dir: Absolute assets directory path or None.
        assets_written: Number of assets created.
        bytes_written_md: Size in bytes of the Markdown written; None in dry-run.
        bytes_written_assets: Sum of asset bytes written; None in dry-run.
        sidecar_written: Whether a sidecar metadata file was created.
        renamed_assets: List of name mappings when collisions were resolved.
        warnings: List of non-fatal anomalies encountered.
        error: Optional error payload {code, message} for non-exceptional failures.
    """

    status: str
    out_md_path: str
    assets_dir: str | None
    assets_written: int
    bytes_written_md: int | None
    bytes_written_assets: int | None
    sidecar_written: bool
    renamed_assets: list[dict[str, str]]
    warnings: list[str]
    error: dict[str, str] | None


class MarkdownSerializerPort(Protocol):
    """Persist a :class:`MarkdownDoc` to disk deterministically.

    Behavior:
        - compute_paths(): plan-only; must not write.
        - write(): atomic writes; concurrency-safe at file level.

    Raises:
        WriteError (path traversal, permission errors, disk full, serialization failure).
    """

    def compute_paths(self, doc: MarkdownDoc, ctx: WriterContext) -> TargetPaths:
        """Compute deterministic output paths for a document without writing.

        Args:
            doc: The document to be written.
            ctx: Writer context controlling mapping and policies.

        Returns:
            TargetPaths: Planned filesystem locations.

        Raises:
            WriteError: On invalid path mapping or policy violations.
        """
        ...

    def write(self, doc: MarkdownDoc, ctx: WriterContext) -> WriteResult:
        """Write the document atomically according to the writer context.

        Args:
            doc: The document to write.
            ctx: Writer context controlling mapping and policies.

        Returns:
            WriteResult: JSON-serializable result summary.

        Raises:
            WriteError: On permission errors, disk full, or serialization failure.
        """
        ...


# ----------------------------
# Forward-compatible ports
# ----------------------------


class TranslatorPort(Protocol):
    """Translate Markdown text nodes only to a target language (default English).

    Behavior:
        - Preserve Markdown structure; translate text nodes deterministically
          for the same input/model.
        - Offline by default; must not expose PII.

    Raises:
        TranslationError, ModelNotAvailableError (alias using TranslationError).
    """

    def translate_markdown(
        self, doc: MarkdownDoc, src_lang: str, tgt_lang: str = "en"
    ) -> MarkdownDoc:
        """Translate text nodes in a MarkdownDoc while preserving structure.

        Args:
            doc: Input MarkdownDoc to translate.
            src_lang: Source language code.
            tgt_lang: Target language code. Defaults to "en".

        Returns:
            MarkdownDoc: New document with translated text nodes.

        Raises:
            TranslationError: When the model is unavailable or the pair unsupported.
        """
        ...


class AnonymizationPort(Protocol):
    """Detect PII and perform deterministic pseudonymization/de-anonymization."""

    def detect(self, text: str, lang: str) -> PiiResult:
        """Detect PII entities in text.

        Args:
            text: Input text to analyze.
            lang: Language code of the text.

        Returns:
            PiiResult: Detected entities with spans and scores.

        Raises:
            AnonymizationError: On langid failures.
        """
        ...

    def pseudonymize(self, text: str, *, context_id: str, lang: str) -> PseudonymizeResult:
        """Deterministically pseudonymize PII in text.

        Args:
            text: Input text containing PII.
            context_id: Stable context used to seed replacements.
            lang: Language code of the text.

        Returns:
            PseudonymizeResult: Pseudonymized text and replacement map.

        Raises:
            AnonymizationError: On pseudonymization failures.
            TokenVaultError: When the token vault is unavailable.
        """
        ...

    def deanonymize(self, text: str, *, context_id: str) -> str:
        """Reverse pseudonymization using a token vault.

        Args:
            text: Pseudonymized text to restore.
            context_id: Context used during pseudonymization.

        Returns:
            str: Restored text.

        Raises:
            TokenVaultError: When de-/re-identification map access fails.
        """
        ...


class VectorBuilderPort(Protocol):
    """Chunk Markdown, compute embeddings, and assemble vector points."""

    def build_points(self, doc: MarkdownDoc, *, variant: str, meta: dict) -> list[Point]:
        """Build vector points with payload from a MarkdownDoc.

        Args:
            doc: Input document to chunk and embed.
            variant: Variant label for downstream consumers (e.g., model name).
            meta: Additional payload fields common to all points.

        Returns:
            list[Point]: Vector points with embedding vectors and payload.

        Raises:
            VectorBuildError: On empty text or dimension mismatch.
        """
        ...


class VectorDBPort(Protocol):
    """Upsert/query vector points in the database (e.g., Qdrant/Faiss)."""

    def upsert(self, points: list[Point], *, collection: str) -> UpsertResult:
        """Upsert vector points into a collection.

        Args:
            points: Points to upsert.
            collection: Collection name.

        Returns:
            UpsertResult: Acknowledgement and counts.

        Raises:
            VectorDBError: On network or schema errors.
        """
        ...

    def ensure_collection(self, collection: str, *, spec: dict | None = None) -> None:
        """Ensure a collection exists with an optional specification.

        Args:
            collection: Collection name to ensure/create.
            spec: Optional collection specification (e.g., vector size/type).

        Raises:
            VectorDBError: On incompatible schema or connectivity issue.
        """
        ...


# ----------------------------
# Typed shapes for forward-compat
# ----------------------------


class PiiEntity(TypedDict):
    kind: str
    start: int
    end: int
    score: float


class PiiResult(TypedDict):
    text: str
    entities: list[PiiEntity]


class PseudonymizeResult(TypedDict):
    text: str
    replacements: dict[str, str]
    context_id: str


class OcrResult(TypedDict):
    text: str
    avg_confidence: float
    by_block: list[dict[str, Any]]


class Point(TypedDict):
    id: str
    vector: list[float]
    payload: dict[str, Any]


class UpsertResult(TypedDict):
    acknowledged: bool
    points_processed: int


# ---------------------------------------------
# English translate ports and error model
# ---------------------------------------------


class LanguageDetectError(DomainError):
    """Unrecoverable language langid failure.

    Usage:
        Implementations of LanguageDetectPort must translate vendor/library errors
        into this domain error. Provide a stable error code and short message.
    """

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("language_detect_error", message, details)


class GlossaryError(DomainError):
    """Glossary rules or I/O failure in term normalization."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("glossary_error", message, details)


class CacheError(DomainError):
    """Cache store access or consistency failure."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("cache_error", message, details)


class LanguageDetectPort(Protocol):
    """Determine the primary language of normalized Markdown text.

    Contract:
        - Deterministic output for identical inputs.
        - Never raises vendor exceptions; raise LanguageDetectError on failure.
        - Accepts any length input; upstream may truncate, but the port does not depend on size.
        - Thread-safe for concurrent calls.

    Concurrency & telemetry:
        May be called concurrently. The optional ``context`` is for logging only.
    """

    def detect(
        self,
        text: str,
        hints: dict[str, Any] | None = None,
        *,
        context: dict[str, Any] | None = None,
    ) -> tuple[str, float]:
        """Detect the primary language of Markdown text.

        Args:
            text: Full Markdown body (LF newlines). Any length is acceptable.
            hints: Optional soft hints such as expected languages or prior metadata,
                e.g., {"candidates": ["sk", "de", "cs"], "path": ".../file.md"}.
            context: Optional telemetry context (doc_id, run_id, path). Ignored if unused.

        Returns:
            tuple[str, float]: (lang_code, confidence) where lang_code is lowercase ISO-like code
            (e.g., "sk", "de", "cs", "pl", "hu", "en") and confidence is a float in [0, 1].

        Raises:
            LanguageDetectError: On unrecoverable langid failure.
        """
        ...


class TranslatePort(Protocol):
    """Produce an English Markdown variant from a source Markdown document.

    Contract:
        - Preserve Markdown structure; do not translate code blocks, inline code, link URLs, or image paths.
        - Deterministic and offline; repeatable runs yield byte-identical output given same inputs/options.
        - Do not mutate the input MarkdownDoc; return a new instance with variant="english" and lang="en".
        - Respect resource limits (segment size/batching) internally; callers may pass options.

    Capabilities:
        Implementations must expose a self-description via ``capabilities()`` for logging/validation.
    """

    def translate_md(
        self,
        doc: MarkdownDoc,
        src_lang: str,
        tgt_lang: str,
        options: dict[str, Any] | None = None,
        *,
        context: dict[str, Any] | None = None,
    ) -> MarkdownDoc:
        """Translate a Markdown document to the target language (project default: English).

        Args:
            doc: Source MarkdownDoc in its original language (variant="original" by convention). Uses
                fields text_md, optional lang, and meta for logging.
            src_lang: Detected/declared source language code (lowercase). Use "auto" if unknown; implementations
                may re-detect but must report final src_lang in metadata.
            tgt_lang: Target language code. Fixed to "en" by current scope but kept parameterized.
            options: Optional translate options, e.g., {"style": "natural"|"literal", "glossary_id": "legal-v1",
                "max_segment_chars": 1000, "max_parallelism": 4}.
            context: Optional telemetry context (doc_id, run_id, path). Ignored if unused.

        Returns:
            MarkdownDoc: New document with:
                - variant="english"
                - lang="en"
                - text_md translated while preserving Markdown structure
                - meta extended with translate metadata (engine, version, time, segment counts,
                  cache hits, src/tgt codes, glossary used)

        Raises:
            TranslationError: When translate fails (engine unavailable, unsupported pair, or internal error).
        """
        ...

    def capabilities(self) -> dict[str, Any]:
        """Return a self-description of the translate engine.

        Returns:
            dict: JSON-serializable engine fingerprint and constraints, for example:
                {
                    "name": "my_offline_mt",
                    "version": "1.2.3",
                    "src_langs": ["sk", "de", "cs", "pl", "hu", "en"],
                    "tgt_langs": ["en"],
                    "max_batch": 32,
                    "max_segment_chars": 2000,
                    "supports_glossary": true,
                    "supports_cache": true
                }
        """
        ...


class GlossaryPort(Protocol):
    """Apply deterministic term normalization pre/post translate.

    Contract:
        - Deterministic, order-stable replacements.
        - Never modifies formatting tokens outside the provided text segment.
        - Must not raise vendor exceptions; raise GlossaryError on failure.
    """

    def apply(
        self,
        text: str,
        *,
        src_lang: str,
        tgt_lang: str,
        mode: str,
        glossary_id: str | None,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Normalize a plain text segment using glossary rules.

        Args:
            text: Plain text segment (not full Markdown) to normalize.
            src_lang: Source language code of the segment.
            tgt_lang: Target language code (for post-translate this will be "en").
            mode: "pre" or "post".
            glossary_id: Identifier of the glossary to use, or None.
            context: Optional telemetry context. Ignored if unused.

        Returns:
            str: Normalized text segment.

        Raises:
            GlossaryError: On rules loading/application failure.
        """
        ...


class CachePort(Protocol):
    """Cache translated segments to reduce work and ensure repeatability.

    Contract:
        - Idempotent and safe under concurrent access.
        - Crash-safe: partial writes must not be surfaced to callers.
    """

    def get(self, key: str) -> str | None:
        """Return a cached value for a deterministic key if present.

        Args:
            key: Deterministic key (e.g., hash of text + src + tgt + engine + glossary_id + mode).

        Returns:
            str | None: Cached translate value or None on miss.

        Raises:
            CacheError: On cache access failure.
        """
        ...

    def put(self, key: str, value: str, ttl: int | None = None) -> None:
        """Store a value under a deterministic key with optional expiration.

        Args:
            key: Deterministic cache key.
            value: Value to store.
            ttl: Optional time-to-live in seconds; implementations may ignore.

        Raises:
            CacheError: On cache write failure.
        """
        ...


# ---------------------------------------------
# New ports and types for top-k language detection, English detection, and text similarity
# ---------------------------------------------


class LanguageDetectTopKCandidate(TypedDict):
    code: str
    score: float


class LanguageDetectTopKResult(TypedDict, total=False):
    """Rich result for language detection using model-only signals.

    Keys:
        lang_code: Selected source language code (lowercase ISO-like).
        confidence: Calibrated confidence in [0,1] for the selected language.
        topk: List of {code, score} pairs from the underlying model (raw scores as returned by the model).
        flags: {"short_text": bool, "low_confidence": bool, "close_top2": bool}
        used_candidates: Optional list of candidate language codes considered.
    """

    lang_code: str
    confidence: float
    topk: list[LanguageDetectTopKCandidate]
    flags: dict[str, bool]
    used_candidates: list[str]


class LanguageDetectTopKPort(Protocol):
    """Model-only top-k language detector with calibrated confidence and flags.

    Deterministic: Identical input and configuration yields identical output.
    """

    def detect_topk(
        self,
        text: str,
        *,
        k: int = 5,
        hints: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> LanguageDetectTopKResult:
        """Return a rich top-k detection result without heuristics.

        Args:
            text: Markdown/plain text to analyze.
            k: Max number of candidates to return (model top-k).
            hints: Optional hints; may include {"candidates": [..]}.
            context: Optional telemetry context.
        """
        ...


class EnglishDetectPort(Protocol):
    """Model-only Englishness detector for validation and probing.

    Contract:
        - english_confidence returns calibrated float in [0,1].
        - Deterministic and offline; no heuristics.
    """

    def english_confidence(self, text: str) -> float:
        """Return probability/confidence that text is English in [0,1]."""
        ...


class SimilarityPort(Protocol):
    """Deterministic similarity scorer for two strings.

    The score must be in [0,1], where 1 means identical and 0 means completely
    dissimilar according to the chosen metric. Implementations must be
    deterministic and must not rely on online resources.
    """

    def similarity(self, a: str, b: str) -> float:
        """Return a similarity score in [0,1] with 1.0 == identical."""
        ...
