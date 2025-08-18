from __future__ import annotations


class PreprocessingError(Exception):
    """Base exception for the preprocessing pipeline."""


class ParserNotFoundError(PreprocessingError):
    """Raised when there is no parser registered for an extension."""


class OcrError(PreprocessingError):
    """Raised when OCR fails (binary, language packs, timeouts)."""


class SerializationError(PreprocessingError):
    """Raised when writing output records fails."""

