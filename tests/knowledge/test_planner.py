"""Tests for the CM-84 multi-hop reasoning loop (``agents.knowledge.planner``).

Covers the AC: the LLM-driven decision policy (action over accumulated passages),
multi-hop accumulation, reformulate->answer and give_up->refuse trajectories,
query dedup, dynamic termination at ``KNOWLEDGE_MAX_STEPS``, the per-iteration
guardrail short-circuit (with ``search_count`` driving the inner-loop cap), the
offline no-credentials contract, and the env-driven selectors.

The planner reads its collaborators (``get_vector_store`` / ``default_embedder``
/ ``retrieve`` / ``answer_question``) from the ``planner`` module namespace, so
we monkeypatch them there; the decision policy is injected via a tiny test planner.
"""

from __future__ import annotations

import pytest
from agents.knowledge import planner as planner_mod
from agents.knowledge.llm import (
    LLMDecisionModel,
    StubChatModel,
    StubDecisionModel,
    get_decision_model,
)
from agents.knowledge.models import KnowledgeDecision, VectorChunk
from agents.knowledge.planner import (
    KNOWLEDGE_MAX_STEPS,
    KNOWLEDGE_QUERY_EMBED_COST_USD,
    LLMKnowledgePlanner,
    StubKnowledgePlanner,
    _LoopPlanner,
    get_knowledge_planner,
)
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


class _FixedStore:
    """Returns the same vector hits on every hop."""

    def __init__(self, vector_hits: list[tuple[VectorChunk, float]]) -> None:
        self._v = vector_hits
        self.calls = 0

    def search_chunks(
        self, tenant_id: str, embedding: list[float], *, top_k: int = 5
    ) -> list[tuple[VectorChunk, float]]:
        self.calls += 1
        return self._v[:top_k]

    def keyword_search(
        self, tenant_id: str, terms: list[str], *, top_k: int = 5
    ) -> list[VectorChunk]:
        return []


class _QueueStore:
    """Returns a different vector-hit list on each hop (one per search_chunks call)."""

    def __init__(self, hits_per_hop: list[list[tuple[VectorChunk, float]]]) -> None:
        self._q = hits_per_hop
        self.calls = 0

    def search_chunks(
        self, tenant_id: str, embedding: list[float], *, top_k: int = 5
    ) -> list[tuple[VectorChunk, float]]:
        hits = self._q[self.calls] if self.calls < len(self._q) else []
        self.calls += 1
        return hits[:top_k]

    def keyword_search(
        self, tenant_id: str, terms: list[str], *, top_k: int = 5
    ) -> list[VectorChunk]:
        return []


class _PolicyPlanner(_LoopPlanner):
    """Stub-answerer loop with an injected decision policy (for trajectory tests)."""

    def __init__(self, decider: object, *, max_steps: int | None = None) -> None:
        super().__init__(max_steps=max_steps)
        self._decider = decider

    def _make_model(self) -> StubChatModel:
        return StubChatModel()

    def _make_decider(self) -> object:
        return self._decider


class _GiveUpDecider:
    cost_per_call_usd = 0.0

    def decide(
        self, question: str, context_blocks: list[str], *, step: int, max_steps: int
    ) -> KnowledgeDecision:
        return KnowledgeDecision(action="give_up", rationale="not in corpus")


class _AlwaysReformulateDecider:
    """Never answers — emits a fresh query each step (drives the bound / guardrail)."""

    cost_per_call_usd = 0.0

    def decide(
        self, question: str, context_blocks: list[str], *, step: int, max_steps: int
    ) -> KnowledgeDecision:
        return KnowledgeDecision(action="reformulate", query=f"followup-{step}")


class _RepeatQueryDecider:
    """Always reformulates to the same query — exercises query dedup."""

    cost_per_call_usd = 0.0

    def decide(
        self, question: str, context_blocks: list[str], *, step: int, max_steps: int
    ) -> KnowledgeDecision:
        return KnowledgeDecision(action="reformulate", query="dup")


def _state(**kw: object) -> AgentState:
    base: dict[str, object] = {"tenant_id": "t1", "request_id": "r1"}
    base.update(kw)
    return AgentState(**base)  # type: ignore[arg-type]


def _wire_store(monkeypatch: pytest.MonkeyPatch, store: object, embedder: object) -> None:
    monkeypatch.setattr(planner_mod, "get_vector_store", lambda: store)
    monkeypatch.setattr(planner_mod, "default_embedder", lambda: embedder)


# --- stub 2-hop trajectory + accumulation -------------------------------------


def test_stub_planner_runs_fixed_two_hop(monkeypatch: pytest.MonkeyPatch) -> None:
    """StubKnowledgePlanner reformulates on hop 0 and answers on hop 1."""
    store = _QueueStore([[(_vc("d1"), 0.8)], [(_vc("d2"), 0.7)]])
    _wire_store(monkeypatch, store, _FakeEmbedder())

    result = StubKnowledgePlanner().run("what are the quiet hours", state=_state())

    assert result.steps == 2
    assert result.searches == 2
    assert store.calls == 2  # two distinct hops retrieved
    assert result.answer.refused is False
    assert result.guardrail_reason is None
    # Stub model is free; cost is two query embeddings.
    assert result.cost_usd == pytest.approx(2 * KNOWLEDGE_QUERY_EMBED_COST_USD)


def test_multi_hop_accumulates_unique_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chunks from both hops reach synthesis, deduped by id."""
    store = _QueueStore([[(_vc("d1"), 0.8)], [(_vc("d2"), 0.7)]])
    _wire_store(monkeypatch, store, _FakeEmbedder())

    captured: dict[str, list] = {}
    real_aq = planner_mod.answer_question

    def spy_answer_question(question, retrieved, *, model):  # type: ignore[no-untyped-def]
        captured["chunks"] = retrieved
        return real_aq(question, retrieved, model=model)

    monkeypatch.setattr(planner_mod, "answer_question", spy_answer_question)

    StubKnowledgePlanner().run("what are the quiet hours", state=_state())

    doc_ids = {rc.chunk.doc_id for rc in captured["chunks"]}
    assert doc_ids == {"d1", "d2"}  # accumulated across both hops


def test_reformulate_then_answer_is_grounded(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _QueueStore(
        [
            [(_vc("d1", text="Pets must be registered with management."), 0.8)],
            [(_vc("d2", text="Dogs over 25kg need board approval."), 0.75)],
        ]
    )
    _wire_store(monkeypatch, store, _FakeEmbedder())

    result = StubKnowledgePlanner().run("can I keep a large dog", state=_state())

    assert result.answer.refused is False
    assert result.answer.citations  # cites a retrieved chunk
    assert result.searches == 2


# --- give_up -> refuse --------------------------------------------------------


def test_give_up_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _FixedStore([(_vc("d1"), 0.8)])
    _wire_store(monkeypatch, store, _FakeEmbedder())

    result = _PolicyPlanner(_GiveUpDecider()).run("obscure question", state=_state())

    assert result.answer.refused is True
    assert result.guardrail_reason is None
    assert result.searches == 1  # hop 0 ran, then the policy gave up
    assert result.steps == 1


# --- dynamic termination / hard step bound ------------------------------------


def test_loop_terminates_at_max_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    """A never-answering policy runs to the bound, one search per fresh query,
    then best-effort synthesizes over what was gathered."""
    store = _FixedStore([(_vc("d1"), 0.8)])
    _wire_store(monkeypatch, store, _FakeEmbedder())

    result = _PolicyPlanner(_AlwaysReformulateDecider(), max_steps=3).run(
        "quiet hours", state=_state()
    )

    assert result.steps == 3
    assert result.searches == 3  # message + followup-0 + followup-1 (each fresh)
    assert result.guardrail_reason is None
    assert result.answer.refused is False  # best-effort synthesis over gathered chunks


# --- query dedup --------------------------------------------------------------


def test_repeated_query_is_not_run_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reformulation to an already-run query is coerced to `answer` (no re-search)."""
    store = _FixedStore([(_vc("d1"), 0.8)])
    _wire_store(monkeypatch, store, _FakeEmbedder())

    result = _PolicyPlanner(_RepeatQueryDecider(), max_steps=5).run(
        "quiet hours", state=_state()
    )

    # hop 0 = "quiet hours", hop 1 = "dup"; the 2nd "dup" repeat coerces to answer.
    assert store.calls == 2
    assert result.searches == 2
    assert result.answer.refused is False


# --- per-iteration guardrail short-circuit ------------------------------------


def test_guardrail_trips_mid_loop_on_search_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """``search_count`` bumps per retrieval, so the CM-26 loop cap trips the inner
    loop. Seeding at the cap lets exactly one hop run before the trip."""
    store = _FixedStore([(_vc("d1"), 0.8)])
    _wire_store(monkeypatch, store, _FakeEmbedder())

    result = _PolicyPlanner(_AlwaysReformulateDecider(), max_steps=10).run(
        "quiet hours", state=_state(search_count=LOOP_CAP)
    )

    assert result.guardrail_reason is not None
    assert "search_count" in result.guardrail_reason
    assert result.searches == 1  # one hop ran, then step 2 tripped the cap


def test_guardrail_trips_before_first_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    """An already-tripped cost cap short-circuits before any retrieval/decision."""
    _wire_store(monkeypatch, object(), object())

    def _boom(*a: object, **k: object) -> object:
        raise AssertionError("must not retrieve/answer after a guardrail trip")

    monkeypatch.setattr(planner_mod, "retrieve", _boom)
    monkeypatch.setattr(planner_mod, "answer_question", _boom)

    result = StubKnowledgePlanner().run("q", state=_state(cost_so_far=COST_CAP_USD + 1.0))

    assert result.guardrail_reason is not None
    assert "cost" in result.guardrail_reason
    assert result.searches == 0
    assert result.answer.refused is True


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


# --- env-driven selectors -----------------------------------------------------


def test_knowledge_planner_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert isinstance(get_knowledge_planner(), StubKnowledgePlanner)
    monkeypatch.setenv("OPENAI_API_KEY", "REPLACE-ME")
    assert isinstance(get_knowledge_planner(), StubKnowledgePlanner)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    assert isinstance(get_knowledge_planner(), LLMKnowledgePlanner)


def test_decision_model_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert isinstance(get_decision_model(), StubDecisionModel)
    monkeypatch.setenv("OPENAI_API_KEY", "REPLACE-ME")
    assert isinstance(get_decision_model(), StubDecisionModel)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    assert isinstance(get_decision_model(), LLMDecisionModel)


def test_stub_decision_model_fixed_trajectory() -> None:
    """The stub policy is a deterministic 2-hop: reformulate then answer."""
    stub = StubDecisionModel()
    hop0 = stub.decide("what are the quiet hours", [], step=0, max_steps=4)
    assert hop0.action == "reformulate"
    assert hop0.query  # a derived follow-up query
    hop1 = stub.decide("what are the quiet hours", ["[1] ..."], step=1, max_steps=4)
    assert hop1.action == "answer"


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
