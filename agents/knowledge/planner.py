"""Iterative reasoning-loop scaffold for the Knowledge Agent (CM-83, Track A).

Jira: CM-83  | Epic: Track A (Knowledge iterative reasoning)  | Phase 1

CM-83 wrapped the single-shot ``retrieve`` -> ``answer_question`` flow in a
bounded ``act -> observe -> decide`` loop, leaving the **decision policy** as a
seam. CM-84 fills that seam with real agentic behaviour: each iteration retrieves
(reusing the original or a reformulated query), accumulates unique chunks across
hops, and asks a :class:`~agents.knowledge.llm.DecisionModel` what to do next —
``answer`` (synthesize over everything gathered and stop), ``reformulate`` /
``search_more`` (run another hybrid retrieval with a new query), or ``give_up``
(refuse). Termination is **dynamic**: the loop ends when the policy answers /
gives up, or on the hard ``KNOWLEDGE_MAX_STEPS`` bound / a guardrail trip — not
after a fixed number of hops.

The loop structure, the step bound, the per-iteration guardrail check, and the
search/cost accounting all live here and stay put; only the policy is
non-deterministic. The offline :class:`StubKnowledgePlanner` pairs a deterministic
:class:`~agents.knowledge.llm.StubDecisionModel` (a fixed 2-hop trajectory) with
the :class:`~agents.knowledge.llm.StubChatModel`, so the whole loop is testable
with no key.

Selector convention mirrors :func:`~agents.knowledge.llm.get_chat_model` and
``get_triage_classifier``: :func:`get_knowledge_planner` returns the real
LLM-driven loop when ``OPENAI_API_KEY`` is set (and not the ``REPLACE-ME``
placeholder), else a deterministic :class:`StubKnowledgePlanner`.

The package stays import-cheap and free of an ``agents.orchestrator`` import at
module load: ``AgentState`` is referenced only for typing (``TYPE_CHECKING``)
and ``guardrails`` is imported lazily inside the loop, so there is no
``knowledge -> orchestrator -> knowledge`` import cycle.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from agents.knowledge.ai_search_store import get_search_store
from agents.knowledge.cosmos_store import get_vector_store
from agents.knowledge.embeddings import Embedder, default_embedder
from agents.knowledge.llm import (
    ChatModel,
    DecisionModel,
    StubChatModel,
    StubDecisionModel,
    get_chat_model,
    get_decision_model,
)
from agents.knowledge.models import SECRET_PLACEHOLDER, KnowledgeAnswer, RetrievedChunk
from agents.knowledge.rag import REFUSAL_TEXT, answer_question
from agents.knowledge.retrieval import SearchStore, merge_unique, retrieve

if TYPE_CHECKING:  # avoid an import cycle — orchestrator imports this package
    from agents.orchestrator.state import AgentState

#: Hard upper bound on loop iterations. Overridable via the ``KNOWLEDGE_MAX_STEPS``
#: env var; exceeding it terminates the loop and falls back to the best grounded
#: answer so far (or a refusal).
KNOWLEDGE_MAX_STEPS: int = 4

#: Flat estimate for one query embedding (text-embedding-3-small, ~20 tokens).
#: Negligible next to the LLM call but counted so the CM-26 cost cap is honest.
#: Lives here (with the rest of the per-call cost model) now that the planner
#: owns the retrieve/answer accounting the ``knowledge`` node used to do inline.
KNOWLEDGE_QUERY_EMBED_COST_USD: float = 0.000001


def _resolve_max_steps() -> int:
    """``KNOWLEDGE_MAX_STEPS`` env override, falling back to the default.

    A non-positive or non-integer value falls back to :data:`KNOWLEDGE_MAX_STEPS`
    so a misconfigured env can never disable the loop bound.
    """
    raw = os.environ.get("KNOWLEDGE_MAX_STEPS", "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            return KNOWLEDGE_MAX_STEPS
        if value > 0:
            return value
    return KNOWLEDGE_MAX_STEPS


def _refusal() -> KnowledgeAnswer:
    """The generic refusal answer (no grounded result available)."""
    return KnowledgeAnswer(answer=REFUSAL_TEXT, confidence=0.0, refused=True)


def _context_blocks(chunks: list[RetrievedChunk]) -> list[str]:
    """Number the accumulated chunks as ``"[n] text"``.

    The shared context format used by both the decision prompt and
    ``answer_question`` so passage numbers line up across the loop.
    """
    return [f"[{i + 1}] {rc.chunk.text}" for i, rc in enumerate(chunks)]


@dataclass(frozen=True)
class PlannerResult:
    """Outcome of one planner run.

    ``guardrail_reason`` is set only when a Stop Rule tripped mid-loop; the
    ``knowledge`` node turns that into a ``guardrail_terminated`` result exactly
    as it would for its own first-statement guardrail check. ``cost_usd`` /
    ``searches`` accumulate across iterations so the node can bump the CM-26
    counters once.

    CM-85: ``reformulations`` (count of fresh reformulated/follow-up queries
    actually issued) and ``termination`` (why the loop ended:
    ``answer`` / ``give_up`` / ``bound`` / ``guardrail`` / ``no_store``) expose
    the trajectory shape so the node can emit the steps/reformulated metrics and
    the trajectory eval can assert it.
    """

    answer: KnowledgeAnswer
    cost_usd: float
    searches: int
    steps: int
    guardrail_reason: str | None = None
    reformulations: int = 0
    termination: str = "answer"


class KnowledgePlanner(Protocol):
    """Runs the Knowledge RAG flow as a bounded reasoning loop."""

    def run(
        self,
        message: str,
        *,
        state: AgentState,
        store: SearchStore | None = None,
        embedder: Embedder | None = None,
    ) -> PlannerResult:
        """Answer ``message`` for ``state``'s tenant, looping per the policy.

        ``store`` / ``embedder`` default to the env-driven factories (the node
        path); the CM-85 trajectory eval injects a deterministic retriever to
        drive the real loop over a synthetic KB.
        """
        ...


class _LoopPlanner:
    """Shared ``act -> observe -> decide`` loop body.

    Subclasses supply the :class:`~agents.knowledge.llm.ChatModel` answerer via
    :meth:`_make_model` and the :class:`~agents.knowledge.llm.DecisionModel`
    policy via :meth:`_make_decider`. Each iteration is wrapped in a
    ``langgraph.node.knowledge.step`` span (CM-85) carrying ``step_index`` /
    ``decision`` / ``query`` / ``top_similarity``; the spans nest under the
    ``knowledge`` node span when run via the orchestrator.
    """

    def __init__(self, *, max_steps: int | None = None) -> None:
        self._max_steps = max_steps if max_steps is not None else _resolve_max_steps()

    # -- seams the subclasses / Track A2 fill in --------------------------------

    def _make_model(self) -> ChatModel:
        """Return the answerer. Created lazily, only once retrieval is possible."""
        raise NotImplementedError

    def _make_decider(self) -> DecisionModel:
        """Return the decision policy. Created lazily, alongside the answerer."""
        raise NotImplementedError

    # -- the loop ---------------------------------------------------------------

    def run(
        self,
        message: str,
        *,
        state: AgentState,
        store: SearchStore | None = None,
        embedder: Embedder | None = None,
    ) -> PlannerResult:
        # Resolve the retriever — injected (eval) or the env-driven factories
        # (node path). No store / embedder (offline / dev / CI) -> cannot
        # retrieve, so refuse WITHOUT any model or network call (CM-28).
        if store is None:
            # CM-100: prefer Azure AI Search (hybrid: BM25 + vector) when
            # configured; fall back to the Cosmos vector store otherwise so
            # CI / offline / un-migrated installs are unchanged.
            store = get_search_store() or get_vector_store()
        if embedder is None:
            embedder = default_embedder()
        if store is None or embedder is None:
            return PlannerResult(_refusal(), 0.0, 0, 0, termination="no_store")

        model = self._make_model()
        decider = self._make_decider()
        searches = 0
        cost = 0.0
        steps = 0
        reformulations = 0
        accumulated: list[RetrievedChunk] = []
        used_queries: set[str] = set()
        query = message
        final: KnowledgeAnswer | None = None
        termination = "answer"

        # Imported lazily so the knowledge package has no import-time dependency
        # on the orchestrator (which imports this package) — no import cycle.
        from agents.observability import langgraph_node_span  # noqa: PLC0415
        from agents.orchestrator import guardrails  # noqa: PLC0415

        for step in range(self._max_steps):
            # Guardrail-first, on every iteration: check the running counters
            # BEFORE any further model/retrieval call so the CM-26 loop/cost caps
            # bound the inner loop just as they bound the node spine.
            loop_state = state.merge(
                {
                    "search_count": state.search_count + searches,
                    "cost_so_far": state.cost_so_far + cost,
                }
            )
            gate = guardrails.check(loop_state)
            if gate.tripped:
                return PlannerResult(
                    final if final is not None else _refusal(),
                    cost,
                    searches,
                    steps,
                    guardrail_reason=gate.reason,
                    reformulations=reformulations,
                    termination="guardrail",
                )

            # CM-85: one span per loop iteration, nested under the node span.
            with langgraph_node_span("knowledge.step", step_index=step) as step_span:
                # Act: retrieve the current query (skip empties / queries already
                # run) and accumulate unique chunks across hops. Each retrieval
                # counts a search so the CM-26 loop cap applies to the inner loop.
                normalized = query.strip().lower()
                if normalized and normalized not in used_queries:
                    used_queries.add(normalized)
                    hits = retrieve(
                        query, tenant_id=state.tenant_id, store=store, embedder=embedder
                    )
                    searches += 1
                    cost += KNOWLEDGE_QUERY_EMBED_COST_USD
                    accumulated = merge_unique(accumulated, hits)
                steps += 1
                top_similarity = max(
                    (rc.similarity for rc in accumulated), default=0.0
                )

                # Think: choose the next action from the evidence gathered so far.
                decision = decider.decide(
                    message,
                    _context_blocks(accumulated),
                    step=step,
                    max_steps=self._max_steps,
                )
                cost += decider.cost_per_call_usd

                step_span.set_attribute("decision", decision.action)
                step_span.set_attribute("query", query)
                step_span.set_attribute("top_similarity", top_similarity)

                if decision.action == "give_up":
                    final = _refusal()
                    termination = "give_up"
                    break
                if decision.action in ("reformulate", "search_more"):
                    nxt = decision.query.strip()
                    if nxt and nxt.lower() not in used_queries:
                        reformulations += 1
                        query = decision.query
                        continue
                    # No fresh query to run -> nothing more to gather; fall
                    # through and answer with what we have (no-progress guard).

                # action == "answer" (or a coerced no-op reformulation): synthesize
                # once over everything accumulated. answer_question scores
                # confidence as the best similarity across the passed chunks ->
                # best-across-hops (CM-47), refusing (grounded) when unsupported.
                final = answer_question(message, accumulated, model=model)
                if accumulated:
                    cost += model.cost_per_call_usd
                termination = "answer"
                break
        else:
            # Step bound exhausted without an explicit answer/give_up — synthesize
            # best-effort over what was gathered (or refuse if nothing was found).
            final = (
                answer_question(message, accumulated, model=model)
                if accumulated
                else _refusal()
            )
            if accumulated:
                cost += model.cost_per_call_usd
            termination = "bound"

        return PlannerResult(
            final if final is not None else _refusal(),
            cost,
            searches,
            steps,
            reformulations=reformulations,
            termination=termination,
        )


class StubKnowledgePlanner(_LoopPlanner):
    """Deterministic offline loop — the no-credentials / CI path.

    Pairs the :class:`~agents.knowledge.llm.StubDecisionModel` (a fixed 2-hop
    trajectory) with the :class:`~agents.knowledge.llm.StubChatModel`, so the full
    reformulate -> retrieve -> accumulate -> answer loop is exercised with no key.
    """

    def _make_model(self) -> ChatModel:
        return StubChatModel()

    def _make_decider(self) -> DecisionModel:
        return StubDecisionModel()


class LLMKnowledgePlanner(_LoopPlanner):
    """Real LLM-driven loop — GPT-4o-mini answerer + decision policy via the seams."""

    def _make_model(self) -> ChatModel:
        return get_chat_model()

    def _make_decider(self) -> DecisionModel:
        return get_decision_model()


def get_knowledge_planner() -> KnowledgePlanner:
    """Env-driven selector — real LLM loop when ``OPENAI_API_KEY`` set, else stub.

    Same convention as :func:`~agents.knowledge.llm.get_chat_model` /
    ``get_triage_classifier``; the ``REPLACE-ME`` placeholder counts as unset.
    """
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key and key != SECRET_PLACEHOLDER:
        return LLMKnowledgePlanner()
    return StubKnowledgePlanner()
