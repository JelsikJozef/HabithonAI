from __future__ import annotations

# Backward-compatibility shim: expose OpenAiClient from the new clients package
from preprocessing.adapters.clients.openai_client import OpenAiClient  # noqa: F401

__all__ = ["OpenAiClient"]

