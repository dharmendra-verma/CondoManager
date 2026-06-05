"""CM-63 — the live pipeline is actually wired to Langfuse + OTel.

Guards the two gaps CM-63 closed:
  1. The agent nodes carry the ``observe_node`` marker (so Langfuse captures a
     per-agent observation), asserted via the name tag the decorator sets on
     both its enabled and disabled paths.
  2. The web-chat entrypoint initializes observability at server startup
     (``configure_otel`` + ``init_langfuse``) — not at import time, and safely
     when the backends are unconfigured.
"""

from __future__ import annotations

import asyncio

import pytest
from agents.observability import langfuse_export as lfe


@pytest.fixture(autouse=True)
def _reset_lfe() -> None:
    lfe._reset_for_tests()
    yield
    lfe._reset_for_tests()


# --- observe_node name tag ---------------------------------------------------


def test_observe_node_tags_name_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lfe, "is_langfuse_enabled", lambda: False)

    @lfe.observe_node("foo.bar")
    def fn(x: int) -> int:
        return x + 1

    assert fn.__langfuse_observed__ == "foo.bar"
    assert fn(1) == 2  # behavior unchanged on the no-op path


def test_observe_node_defaults_marker_to_function_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lfe, "is_langfuse_enabled", lambda: False)

    @lfe.observe_node()
    def my_node() -> None:
        return None

    assert my_node.__langfuse_observed__ == "my_node"


# --- the agent nodes are decorated -------------------------------------------


def test_agent_nodes_carry_observe_marker() -> None:
    """Every agent node in the spine is wrapped by @observe_node (CM-63)."""
    from agents.orchestrator import nodes

    expected = {
        "triage": "triage",
        "knowledge": "knowledge",
        "maintenance": "maintenance",
        "vendor": "vendor",
        "escalation": "escalation",
        "hitl_review": "hitl_review",
    }
    for attr, obs_name in expected.items():
        fn = getattr(nodes, attr)
        assert getattr(fn, "__langfuse_observed__", None) == obs_name, attr


# --- entrypoint initializes observability ------------------------------------


def test_webchat_lifespan_initializes_observability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The web-chat lifespan calls configure_otel + init_langfuse on startup."""
    from agents.webchat import app as webchat_app

    calls: list[str] = []
    monkeypatch.setattr(webchat_app, "configure_otel", lambda **_: calls.append("otel"))
    monkeypatch.setattr(webchat_app, "init_langfuse", lambda: calls.append("langfuse"))

    async def _run() -> None:
        async with webchat_app._lifespan(webchat_app.app):
            pass

    asyncio.run(_run())
    assert calls == ["otel", "langfuse"]
