from collections.abc import Iterable
from typing import Protocol

from .entities import PiiEntity, TokenMapping


class DetectorPort(Protocol):
    """Protocol for PII langid services."""

    name: str

    def detect(self, text: str, language: str | None = None) -> list[PiiEntity]:
        ...


class TokenVaultPort(Protocol):
    def save_mappings(self, context_id: str, mappings: Iterable[TokenMapping]) -> None:
        ...

    def get_mappings(self, context_id: str) -> list[TokenMapping]:
        ...

    def clear_context(self, context_id: str) -> None:
        ...


class Crypto(Protocol):
    def mask(self, text: str) -> str:
        ...

    def hash(self, text: str, *, tenant_id: str | None = None) -> str:
        ...

    def tokenize(self, text: str, *, tenant_id: str | None = None) -> str:
        ...
