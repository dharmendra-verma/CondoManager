"""Tests for the CM-83 reasoning-loop scaffold (``agents.knowledge.planner``).

Covers the AC: stub-equals-legacy single-shot output (golden), the hard
``KNOWLEDGE_MAX_STEPS`` bound, the per-iteration guardrail short-circuit (and
that ``search_count`` drives the inner-loop CM-26 cap), the offline
no-credentials contract, and the env-driven selector.

The planner reads its collaborators (``get_vector_store`` / ``default_embedder``
/ ``retrieve`` / ``answer_question``) from the ``planner`` module namespace, so
we monkeypatch them there to drive each branch deterministically.
"""

from __future__ import annotations

import pytest
from agents.knowledge import planner as planner_mod
from agents.knowledge.llm import StubChatModel
from agents.knowledge.models import VectorChunk
from agents.knowledge.planner import (
    KNOWLEDGE_MAX_STEPS,
    KNOWLEDGE_QUERY_EMBED_COST_USD,
    LLMKnowledgePlanner,
    StubKnowledgePlanner,
    _LoopPlanner,
    get_knowledge_planner,
)
from agents.knowledge.rag import answer_question
from agents.knowledge.retrieval import retrieve
from agents.orchestrator.guardrails import COST_CAP_USD, LOOP_CAP
from agents.orchestrator.state import AgentState


def _vc(doc_id: str, *, idx: int = 0, text: str = "Quiet hours are 10pm to 7am.") -> VectorChunk:
    return VectorChunk(
        id=f"t1:{doc_id}:{idx}",
        tenantId="t1",
        doc_id=doc_id,
        doc_title=f"Doc {doc_id}",
        chunk_index=idx,
        text=text,
        embedding=[0.1, 0.2],
        content_hash="h",
        doc_version=1,
        source="gdrive",
        ts="2026-05-29T00:00:00+00:00",
    )


class _FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0, 0.5] for _ in texts]


class _FakeStore:
    def __init__(
        self,
        vector_hits: list[tuple[VectorChunk, float]],
        keyword_hits: list[VectorChunk] | None = None,
    ) -> None:
        self._v = vector_hits
        self._k = keyword_hits or []

    def search_chunks(
        self, tenant_id: str, embedding: list[float], *, top_k: int = 5
    ) -> list[tuple[VectorChunk, float]]:
        return self._v[:top_k]

    def keyword_search(
        self, tenant_id: str, terms: list[str], *, top_k: int = 5
    ) -> list[VectorChunk]:
        return self._k[:top_k]


class _NeverStopStubPlanner(_LoopPlanner):
    """Stub-backed loop whose policy never stops — exercises the hard bound."""

    def _make_model(self) -> StubChatModel:
        return StubChatModel()

    def _decide(self, answer: object, step: int) -> bool:
        return False


def _state(**kw: object) -> AgentState:
    base: dict[str, object] = {"tenant_id": "t1", "request_id": "r1"}
    base.update(kw)
    return AgentState(**base)  # type: ignore[arg-type]


def _wire_store(monkeypatch: pytest.MonkeyPatch, store: object, embedder: object) -> None:
    monkeypatch.setattr(planner_mod, "get_vector_store", lambda: store)
    monkeypatch.setattr(planner_mod, "default_embedder", lambda: embedder)


# --- stub == legacy golden ----------------------------------------------------


def test_stub_planner_matches_legacy_single_shot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Single-pass StubKnowledgePlanner output is byte-identical to the legacy
    ``retrieve`` + ``answer_question`` single shot (answer, searches, cost)."""
    store = _FakeStore(vector_hits=[(_vc("d1"), 0.8)])
    embedder = _FakeEmbedder()
    _wire_store(monkeypatch, store, embedder)

    message = "what are the quiet hours"
    # Legacy reference path.
    retrieved = retrieve(message, tenant_id="t1", store=store, embedder=embedder)
    legacy = answer_question(message, retrieved, model=StubChatModel())

    result = StubKnowledgePlanner().run(message, state=_state())

    assert result.answer == legacy
    assert result.answer.refused is False
    assert result.searches == 1
    assert result.steps == 1
    assert result.guardrail_reason is None
    # Stub model is free, so the only cost is the one query embedding.
    assert result.cost_usd == pytest.approx(KNOWLEDGE_QUERY_EMBED_COST_USD)


def test_default_policy_is_single_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even with the default 4-step bound, the default policy stops after one."""
    store = _FakeStore(vector_hits=[(_vc("d1"), 0.8)])
    _wire_store(monkeypatch, store, _FakeEmbedder())

    result = StubKnowledgePlanner(max_steps=4).run("quiet hours", state=_state())

    assert result.steps == 1
    assert result.searches == 1


# --- offline / no-credentials contract ----------------------------------------


def test_offline_refuses_without_retrieval_or_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire_store(monkeypatch, None, None)

    def _boom(*a: object, **k: object) -> object:
        raise AssertionError("must not retrieve/answer when unconfigured")

    monkeypatch.setattr(planner_mod, "retrieve", _boom)
    monkeypatch.setattr(planner_mod, "answer_question", _boom)

    result = StubKnowledgePlanner().run("q", state=_state(search_count=2, cost_so_far=1.0))

    assert result.answer.refused is True
    assert result.searches == 0
    assert result.cost_usd == 0.0
    assert result.steps == 0
    assert result.guardrail_reason is None


# --- hard step bound ----------------------------------------------------------


def test_loop_terminates_at_max_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    """A never-stop policy runs exactly ``max_steps`` times, one search each,
    and falls back to the best grounded answer so far."""
    store = _FakeStore(vector_hits=[(_vc("d1"), 0.8)])
    _wire_store(monkeypatch, store, _FakeEmbedder())

    result = _NeverStopStubPlanner(max_steps=3).run("quiet hours", state=_state())

    assert result.steps == 3
    assert result.searches == 3
    assert result.guardrail_reason is None
    assert result.answer.refused is False
    assert result.cost_usd == pytest.approx(3 * KNOWLEDGE_QUERY_EMBED_COST_USD)


# --- per-iteration guardrail short-circuit ------------------------------------


def test_guardrail_trips_mid_loop_on_search_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """``search_count`` is bumped per retrieval, so the CM-26 loop cap trips the
    inner loop. Seeding at the cap lets exactly one search run before the trip."""
    store = _FakeStore(vector_hits=[(_vc("d1"), 0.8)])
    _wire_store(monkeypatch, store, _FakeEmbedder())

    result = _NeverStopStubPlanner(max_steps=10).run(
        "quiet hours", state=_state(search_count=LOOP_CAP)
    )

    assert result.guardrail_reason is not None
    assert "search_count" in result.guardrail_reason
    assert result.searches == 1  # one retrieval ran, then step 2 tripped
    # The grounded answer from the completed iteration is carried back.
    assert result.answer.refused is False


def test_guardrail_trips_before_first_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    """An already-tripped cost cap short-circuits before any model/retrieval call."""
    _wire_store(monkeypatch, object(), object())

    def _boom(*a: object, **k: object) -> object:
        raise AssertionError("must not retrieve/answer after a guardrail trip")

    monkeypatch.setattr(planner_mod, "retrieve", _boom)
    monkeypatch.setattr(planner_mod, "answer_question", _boom)

    result = StubKnowledgePlanner().run("q", state=_state(cost_so_far=COST_CAP_USD + 1.0))

    assert result.guardrail_reason is not None
    assert "cost" in result.guardrail_reason
    assert result.searches == 0
    assert result.answer.refused is True  # no grounded answer yet


# --- env-driven selector ------------------------------------------------------


def test_selector_returns_stub_when_key_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert isinstance(get_knowledge_planner(), StubKnowledgePlanner)


def test_selector_treats_placeholder_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "REPLACE-ME")
    assert isinstance(get_knowledge_planner(), StubKnowledgePlanner)


def test_selector_returns_llm_planner_when_keyed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    planner = get_knowledge_planner()
    assert isinstance(planner, LLMKnowledgePlanner)


# --- KNOWLEDGE_MAX_STEPS resolution -------------------------------------------


def test_max_steps_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOWLEDGE_MAX_STEPS", "7")
    assert StubKnowledgePlanner()._max_steps == 7


def test_max_steps_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KNOWLEDGE_MAX_STEPS", raising=False)
    assert StubKnowledgePlanner()._max_steps == KNOWLEDGE_MAX_STEPS
    assert KNOWLEDGE_MAX_STEPS == 4


def test_max_steps_ignores_invalid_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for bad in ("0", "-3", "abc", ""):
        monkeypatch.setenv("KNOWLEDGE_MAX_STEPS", bad)
        assert StubKnowledgePlanner()._max_steps == KNOWLEDGE_MAX_STEPS
