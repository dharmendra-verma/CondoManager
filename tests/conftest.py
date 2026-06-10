"""Repo-wide pytest fixtures.

Determinism guard (CM-79): the agent ``get_*`` selectors now choose the real LLM
when *either* Azure OpenAI (``AZURE_OPENAI_ENDPOINT`` + ``AZURE_OPENAI_API_KEY``)
or ``OPENAI_API_KEY`` is configured (see :func:`agents.chat.llm_configured`). CI
sets none of these, but a developer may have them exported in their shell — which
would flip the offline suite onto the network and flake (the CM-92 lesson). This
autouse fixture clears every LLM-provider env var before each test so the suite
is deterministically offline by default; a test that wants the LLM path sets the
relevant var itself via ``monkeypatch.setenv`` (which runs after this autouse
fixture, so it still takes effect).
"""

from __future__ import annotations

import pytest

#: Every env var the chat-model factory / embedder selectors read.
_LLM_ENV_VARS = (
    "OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_CHAT_DEPLOYMENT",
    "AZURE_OPENAI_API_VERSION",
    # CM-100: Azure AI Search store selection — clear so the knowledge planner
    # stays on the offline/Cosmos path unless a test opts in.
    "AZURE_SEARCH_ENDPOINT",
    "AZURE_SEARCH_KEY",
    "AZURE_SEARCH_INDEX",
)


@pytest.fixture(autouse=True)
def _offline_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear all LLM-provider env vars so each test is offline unless it opts in."""
    for var in _LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
