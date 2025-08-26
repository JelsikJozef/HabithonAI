import os
import sys
import pathlib
import pytest

# Ensure project root and src/ are importable
ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for p in (ROOT, SRC):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

@pytest.fixture(autouse=True)
def _offline_env(monkeypatch):
    # Enforce offline behavior for any libraries that might attempt downloads
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("NO_NETWORK", "1")
    # Disable tokenizers parallelism which can be noisy in tests
    monkeypatch.setenv("TOKENIZERS_PARALLELISM", "false")
    yield

@pytest.fixture()
def fixtures_dir() -> pathlib.Path:
    return pathlib.Path(__file__).parent / "fixtures"

@pytest.fixture()
def md_fixture(fixtures_dir):
    def _load(name: str) -> str:
        p = fixtures_dir / "md" / name
        return p.read_text(encoding="utf-8")
    return _load
