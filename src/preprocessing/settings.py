from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import os

try:  # optional dependency
    from dotenv import load_dotenv, find_dotenv  # type: ignore
except Exception:  # pragma: no cover
    def load_dotenv(*args, **kwargs):  # type: ignore
        return False
    def find_dotenv(*args, **kwargs):  # type: ignore
        return ""


@dataclass(frozen=True)
class Settings:
    """Application settings loaded once from environment or .env.

    Only this module should load .env. All other modules should receive
    configuration via an instance of this class.
    """

    openai_api_key: str
    openai_model: str = "gpt-4o-mini"
    openai_max_tokens: int = 512
    anonymization_include_text: bool = False

    @staticmethod
    def _to_bool(s: Optional[str], default: bool = False) -> bool:
        if s is None:
            return default
        return str(s).strip().lower() in {"1", "true", "yes", "y", "on"}

    @classmethod
    def load(cls, *, use_dotenv: bool = True) -> "Settings":
        """Load settings from the environment, optionally loading .env first.

        - Requires OPENAI_API_KEY; raises if missing.
        - OPENAI_MODEL defaults to 'gpt-4o-mini'.
        - OPENAI_MAX_TOKENS defaults to 512.
        - ANON_INCLUDE_TEXT controls whether to include raw text in outputs alongside pseudonymized text.
        """
        if use_dotenv:
            try:
                dotenv_path = find_dotenv()
                if dotenv_path:
                    load_dotenv(dotenv_path)
                else:
                    load_dotenv()
            except Exception:
                # Best-effort; continue without .env
                pass
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "OPENAI_API_KEY is required but not set. Set it in your environment or .env file."
            )
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        try:
            max_tokens = int(os.environ.get("OPENAI_MAX_TOKENS", "512"))
        except Exception:
            max_tokens = 512
        include_text = cls._to_bool(os.environ.get("ANON_INCLUDE_TEXT"), default=False)
        return cls(
            openai_api_key=key,
            openai_model=model,
            openai_max_tokens=max_tokens,
            anonymization_include_text=include_text,
        )

