"""Tests for the shared chat-model factory (CM-79).

Covers the provider-detection predicate (:func:`agents.chat.llm_configured`) and
the client builder (:func:`agents.chat.build_chat_model`) across the three
configurations — Azure OpenAI (prod), OpenAI direct (local), and neither
(offline). Construction is offline: building a langchain client does not call the
network (that only happens on ``invoke``), so the type + key attributes are
assertable with fake credentials. The repo-wide ``_offline_llm_env`` autouse
fixture (tests/conftest.py) clears every LLM env var first, so each test starts
from a known-empty state and sets only what it needs.
"""

from __future__ import annotations

import pytest
from agents.chat import (
    DEFAULT_AZURE_CHAT_API_VERSION,
    DEFAULT_CHAT_MODEL,
    build_chat_model,
    llm_configured,
)

_ENDPOINT = "https://example.openai.azure.com/"


def _set_azure(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", _ENDPOINT)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "az-fake-key")
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)


# --- llm_configured ----------------------------------------------------------


def test_llm_configured_false_when_nothing_set() -> None:
    assert llm_configured() is False


def test_llm_configured_true_with_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    assert llm_configured() is True


def test_llm_configured_placeholder_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "REPLACE-ME")
    assert llm_configured() is False


def test_llm_configured_true_with_both_azure_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_azure(monkeypatch)
    assert llm_configured() is True


def test_llm_configured_false_with_only_azure_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    # Endpoint without the key (or vice-versa) is a half-config — fails closed.
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", _ENDPOINT)
    assert llm_configured() is False


def test_llm_configured_azure_placeholder_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", _ENDPOINT)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "REPLACE-ME")
    assert llm_configured() is False


# --- build_chat_model --------------------------------------------------------


def test_build_raises_when_unconfigured() -> None:
    with pytest.raises(RuntimeError, match="no LLM provider configured"):
        build_chat_model()


def test_build_returns_openai_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    client = build_chat_model("gpt-4o-mini")
    assert type(client).__name__ == "ChatOpenAI"


def test_build_returns_azure_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_azure(monkeypatch)
    client = build_chat_model(DEFAULT_CHAT_MODEL)
    assert type(client).__name__ == "AzureChatOpenAI"
    # Deployment defaults to the model name (prod deploys the model under it).
    assert client.deployment_name == DEFAULT_CHAT_MODEL
    assert client.openai_api_version == DEFAULT_AZURE_CHAT_API_VERSION


def test_build_azure_honours_deployment_and_version_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_azure(
        monkeypatch,
        AZURE_OPENAI_CHAT_DEPLOYMENT="my-chat-deploy",
        AZURE_OPENAI_API_VERSION="2025-01-01",
    )
    client = build_chat_model("gpt-4o-mini")
    assert client.deployment_name == "my-chat-deploy"
    assert client.openai_api_version == "2025-01-01"


def test_azure_takes_precedence_over_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    # Both configured → Azure wins (prod is the multi-credential case).
    _set_azure(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    assert type(build_chat_model()).__name__ == "AzureChatOpenAI"
