from __future__ import annotations

from typing import Any

__all__ = [
    "DomainError",
    "SettingsError",
    "FactoryError",
    "UnsupportedFormatError",
    "ParserDependencyMissingError",
    "CorruptedFileError",
    "PasswordProtectedFileError",
    "ParseWarningAsError",
    "NormalizationError",
    "WriteError",
    "OcrError",
    "TranslationError",
    "AnonymizationError",
    "TokenVaultError",
    "EnrichmentError",
    "VectorBuildError",
    "VectorDBError",
    "NotConfigured",
    # Backward-compatible aliases/classes
    "PreprocessingError",
    "ParserNotFoundError",
    "SerializationError",
]


class DomainError(Exception):
    """Base class for all domain errors in preprocessing.

    Each error carries a stable machine-readable ``code``, a human-readable
    ``message``, and optional structured ``details``. All fields are safe to
    serialize and intended for logs, reports, or APIs.

    Args:
        code: Stable error code in snake_case (e.g., ``"unsupported_format"``).
        message: Human-readable, actionable description.
        details: Optional JSON-serializable structured context (e.g., ``path``,
            ``extension``, ``adapter_key``, ``setting_name``).

    Notes:
        - ``details`` must not include secrets; callers should redact upstream.
        - ``to_dict()`` omits ``details`` when it is ``None``.
    """

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic dictionary representation of the error.

        Returns:
            dict: ``{"code": code, "message": message, "details": details}`` with
            ``details`` omitted when ``None``.
        """
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            out["details"] = self.details
        return out

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.code}: {self.message}"


# ----------------------------
# Configuration & wiring
# ----------------------------


class SettingsError(DomainError):
    """Invalid or conflicting settings detected.

    Usage:
        Raise when configuration values are missing, malformed, or mutually
        incompatible. Include ``setting_name`` and relevant values in ``details``.

    Raises:
        This class itself is raised by adapters/app layer; vendor exceptions
        must be translated into this domain error.

    Suggested HTTP mapping (informational): 400 Bad Request.
    """

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("settings_error", message, details)


class FactoryError(DomainError):
    """Dependency preflight or wiring conflict preventing adapter construction.

    Include missing binaries/models or environment preconditions in ``details``.

    Suggested HTTP mapping (informational): 500 Internal Server Error.
    """

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("factory_error", message, details)


# ----------------------------
# Parsing / conversion
# ----------------------------


class UnsupportedFormatError(DomainError):
    """Extension or MIME type is not supported by the system.

    Include the offending ``extension``/``mime`` in ``details`` and optionally
    the list of ``supported`` values.

    Suggested HTTP mapping: 422 Unprocessable Entity.
    """

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("unsupported_format", message, details)


class ParserDependencyMissingError(DomainError):
    """Required local library/binary is missing for the selected parser.

    Example remediation: "Install tesseract and 'eng' language pack".
    """

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("parser_dependency_missing", message, details)


class CorruptedFileError(DomainError):
    """Input file is unreadable or structurally broken (e.g., truncated ZIP)."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("corrupted_file", message, details)


class PasswordProtectedFileError(DomainError):
    """Encrypted file requires a password to open (e.g., PDF/DOCX)."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("password_protected", message, details)


class ParseWarningAsError(DomainError):
    """Raised in strict modes where a lossy conversion would have occurred."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("parse_warning_as_error", message, details)


# ----------------------------
# Encoding / normalization
# ----------------------------


class NormalizationError(DomainError):
    """Normalization failure (invalid options, size limit exceeded, fence errors)."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("normalization_error", message, details)


# ----------------------------
# Serialization (writing)
# ----------------------------


class WriteError(DomainError):
    """Write-time failure (path traversal, permission denied, disk full, JSON failure).

    Include paths and actionable remediation steps in the ``message``/``details``.

    Suggested HTTP mapping: 500 Internal Server Error.
    """

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("write_error", message, details)


# ----------------------------
# OCR / Translation / Anonymization / Enrichment / Vector
# ----------------------------


class OcrError(DomainError):
    """OCR engine/packs missing or recognition failure."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("ocr_error", message, details)


class TranslationError(DomainError):
    """Translation model unavailable or unsupported language pair."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("translation_error", message, details)


class AnonymizationError(DomainError):
    """Detection or pseudonymization failure."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("anonymization_error", message, details)


class TokenVaultError(DomainError):
    """De-/re-identification map access errors (e.g., missing key, storage failure)."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("token_vault_error", message, details)


class EnrichmentError(DomainError):
    """Non-JSON or schema-invalid LLM output in enrichment stage."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("enrichment_error", message, details)


class VectorBuildError(DomainError):
    """Chunking/embedding assembly problems (empty text, dimension mismatch)."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("vector_build_error", message, details)


class VectorDBError(DomainError):
    """Upsert/query/collection errors (network unavailable or schema mismatch)."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("vector_db_error", message, details)


# ----------------------------
# Utility
# ----------------------------


class NotConfigured(DomainError):
    """Feature is intentionally disabled or not wired in this deployment.

    Adapters implementing forward-compatible ports should raise this when the
    capability is not configured.
    """

    def __init__(
        self, message: str = "feature not configured", details: dict[str, Any] | None = None
    ) -> None:
        super().__init__("not_configured", message, details)


# ----------------------------
# Backward-compatible legacy classes
# ----------------------------


class PreprocessingError(DomainError):
    """Legacy base exception maintained for backward compatibility.

    Notes:
        Prefer catching :class:`DomainError`. This alias preserves older imports
        (``from ...domain.errors import PreprocessingError``).
    """

    def __init__(
        self, message: str = "preprocessing error", details: dict[str, Any] | None = None
    ) -> None:
        super().__init__("preprocessing_error", message, details)


class ParserNotFoundError(UnsupportedFormatError):
    """Legacy alias used by the parser registry when extension is unknown.

    Notes:
        Prefer :class:`UnsupportedFormatError` in new code.
    """

    def __init__(
        self, message: str = "parser not found", details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, {**(details or {}), "deprecated": True})


class SerializationError(WriteError):
    """Legacy alias for write-time serialization failures.

    Notes:
        Prefer :class:`WriteError` in new code for all writer/sidecar issues.
    """

    def __init__(
        self, message: str = "serialization error", details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, {**(details or {}), "deprecated": True})
