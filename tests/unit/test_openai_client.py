from __future__ import annotations

import json
from typing import Any, Dict

import builtins

import types

import pytest

from src.shared.llm import openai_client as oc


class _FakeResponse:
    def __init__(self, text: str, usage: Dict[str, Any] | None = None) -> None:
        self.output_text = text
        self.usage = usage or {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
        # also emulate nested structure fallback
        self.output = [types.SimpleNamespace(content=[types.SimpleNamespace(text=text)])]


class _FakeResponsesAPI:
    def __init__(self) -> None:
        self.last_kwargs: Dict[str, Any] | None = None
        self.should_raise = False

    def create(self, **kwargs):  # type: ignore[no-untyped-def]
        self.last_kwargs = dict(kwargs)
        if self.should_raise:
            raise RuntimeError("simulated responses failure")
        payload = json.dumps({"summary": "This is a test.", "keywords": ["a", "b", "c", "d", "e"]})
        return _FakeResponse(payload)


class _FakeChatCompletions:
    def __init__(self) -> None:
        self.last_kwargs: Dict[str, Any] | None = None

    def create(self, **kwargs):  # type: ignore[no-untyped-def]
        self.last_kwargs = dict(kwargs)
        payload = json.dumps({"summary": "This is a test.", "keywords": ["a", "b", "c", "d", "e"]})
        fake_msg = types.SimpleNamespace(content=payload)
        fake_chat = types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=fake_msg)],
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        )
        return fake_chat


class _FakeClient:
    def __init__(
        self,
        responses_api: _FakeResponsesAPI | None = None,
        chat_comp: _FakeChatCompletions | None = None,
    ) -> None:
        self.responses = responses_api or _FakeResponsesAPI()
        self.chat = types.SimpleNamespace(completions=(chat_comp or _FakeChatCompletions()))


@pytest.fixture(autouse=True)
def _set_api_key_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")


def test_gpt5_omits_sampling_params_in_responses_api(monkeypatch):
    fake_resp = _FakeResponsesAPI()
    fake_client = _FakeClient(responses_api=fake_resp)
    monkeypatch.setattr(oc, "_build_client", lambda: fake_client)

    out = oc.summarize_keywords("hello", model="gpt-5-mini", timeout_s=5, seed=0)
    assert out["summary"].endswith(".")
    assert fake_resp.last_kwargs is not None
    # Ensure omitted sampling params for gpt-5
    for k in ("temperature", "top_p", "seed"):
        assert k not in fake_resp.last_kwargs


def test_gpt4_includes_sampling_params_in_responses_api(monkeypatch):
    fake_resp = _FakeResponsesAPI()
    fake_client = _FakeClient(responses_api=fake_resp)
    monkeypatch.setattr(oc, "_build_client", lambda: fake_client)

    out = oc.summarize_keywords("hello", model="gpt-4o-mini", timeout_s=5, seed=42)
    assert out["summary"].endswith(".")
    assert fake_resp.last_kwargs is not None
    # Ensure deterministic params for gpt-4 family
    assert fake_resp.last_kwargs.get("temperature") == 0
    assert fake_resp.last_kwargs.get("top_p") == 1
    assert fake_resp.last_kwargs.get("seed") == 42


def test_fallback_to_chat_completions_respects_rules(monkeypatch):
    fake_resp = _FakeResponsesAPI()
    fake_resp.should_raise = True  # force fallback
    fake_chat = _FakeChatCompletions()
    fake_client = _FakeClient(responses_api=fake_resp, chat_comp=fake_chat)
    monkeypatch.setattr(oc, "_build_client", lambda: fake_client)

    out = oc.summarize_keywords("hello", model="gpt-5-mini", timeout_s=5, seed=7)
    assert out["summary"].endswith(".")
    assert fake_chat.last_kwargs is not None
    for k in ("temperature", "top_p", "seed"):
        assert k not in fake_chat.last_kwargs

    # And for gpt-4o-mini includes params
    fake_resp.should_raise = True
    fake_chat.last_kwargs = None
    out = oc.summarize_keywords("hello", model="gpt-4o-mini", timeout_s=5, seed=9)
    assert out["summary"].endswith(".")
    assert fake_chat.last_kwargs is not None
    assert fake_chat.last_kwargs.get("temperature") == 0
    assert fake_chat.last_kwargs.get("top_p") == 1
    assert fake_chat.last_kwargs.get("seed") == 9
