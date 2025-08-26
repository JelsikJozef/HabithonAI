from typing import List, Protocol, Iterable, Optional, Tuple
from .entities import PiiEntity, TokenMapping


class DetectorPort(Protocol):
    """Protocol for PII langid services."""
    name: str

    def detect(self, text: str, language: Optional[str] = None) -> List[PiiEntity]:
        ...


class TokenVaultPort(Protocol):
    def save_mappings(self, context_id: str, mappings: Iterable[TokenMapping]) -> None:
        ...

    def get_mappings(self, context_id: str) -> List[TokenMapping]:
        ...

    def clear_context(self, context_id: str) -> None:
        ...


class Crypto(Protocol):
    def mask(self, text: str) -> str:
        ...

    def hash(self, text: str, *, tenant_id: Optional[str] = None) -> str:
        ...

    def tokenize(self, text: str, *, tenant_id: Optional[str] = None) -> str:
        ...
