"""Iterative reasoning-loop scaffold for the Knowledge Agent (CM-83, Track A).

Jira: CM-83  | Epic: Track A (Knowledge iterative reasoning)  | Phase 1

Today's Knowledge flow is single-shot: ``retrieve`` -> ``answer_question`` ->
refuse below :data:`~agents.knowledge.models.CONFIDENCE_THRESHOLD`. This module
wraps that flow in a bounded ``decide -> act -> observe`` loop so a later story
(Track A2) can let the agent "try harder" by swapping only the **decision
policy** — the loop structure, the hard step bound, the per-iteration guardrail
check, and the search/cost accounting all live here and stay put.

The default policy is **"answer once, then stop"**, so the loop runs exactly one
iteration and the output is byte-identical to the pre-CM-83 single-shot path
(pinned by a stub-equals-legacy golden test).

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

from agents.knowledge.cosmos_store import get_vector_store
from agents.knowledge.embeddings import default_embedder
from agents.knowledge.llm import ChatModel, StubChatModel, get_chat_model
from agents.knowledge.models import SECRET_PLACEHOLDER, KnowledgeAnswer
from agents.knowledge.rag import REFUSAL_TEXT, answer_question
from agents.knowledge.retrieval import retrieve

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


def _prefer(current: KnowledgeAnswer | None, candidate: KnowledgeAnswer) -> KnowledgeAnswer:
    """Keep the best grounded answer seen so far.

    A non-refused answer always beats a refusal; among grounded answers the
    higher confidence wins. When every iteration refuses, the first refusal is
    retained so the fallback is still a well-formed :class:`KnowledgeAnswer`.
    """
    if current is None:
        return candidate
    if candidate.refused:
        return current
    if current.refused or candidate.confidence > current.confidence:
        return candidate
    return current


@dataclass(frozen=True)
class PlannerResult:
    """Outcome of one planner run.

    ``guardrail_reason`` is set only when a Stop Rule tripped mid-loop; the
    ``knowledge`` node turns that into a ``guardrail_terminated`` result exactly
    as it would for its own first-statement guardrail check. ``cost_usd`` /
    ``searches`` accumulate across iterations so the node can bump the CM-26
    counters once.
    """

    answer: KnowledgeAnswer
    cost_usd: float
    searches: int
    steps: int
    guardrail_reason: str | None = None


class KnowledgePlanner(Protocol):
    """Runs the Knowledge RAG flow as a bounded reasoning loop."""

    def run(self, message: str, *, state: AgentState) -> PlannerResult:
        """Answer ``message`` for ``state``'s tenant, looping per the policy."""
        ...


class _LoopPlanner:
    """Shared ``decide -> act -> observe`` loop body.

    Subclasses supply the :class:`~agents.knowledge.llm.ChatModel` via
    :meth:`_make_model`; the default decision policy (:meth:`_decide`) stops
    after the first answer, so behaviour is single-pass until Track A2 overrides
    the policy.
    """

    def __init__(self, *, max_steps: int | None = None) -> None:
        self._max_steps = max_steps if max_steps is not None else _resolve_max_steps()

    # -- seams the subclasses / Track A2 fill in --------------------------------

    def _make_model(self) -> ChatModel:
        """Return the answerer. Created lazily, only once retrieval is possible."""
        raise NotImplementedError

    def _decide(self, answer: KnowledgeAnswer, step: int) -> bool:
        """Return ``True`` to stop the loop. Default policy: stop after one pass."""
        return True

    # -- the loop ---------------------------------------------------------------

    def run(self, message: str, *, state: AgentState) -> PlannerResult:
        # No store / embedder (offline / dev / CI) -> cannot retrieve, so refuse
        # WITHOUT any model or network call (the CM-28 no-credentials contract).
        store = get_vector_store()
        embedder = default_embedder()
        if store is None or embedder is None:
            return PlannerResult(_refusal(), 0.0, 0, 0)

        model = self._make_model()
        searches = 0
        cost = 0.0
        steps = 0
        best: KnowledgeAnswer | None = None

        # Imported lazily so the knowledge package has no import-time dependency
        # on the orchestrator (which imports this package) — no import cycle.
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
                    best if best is not None else _refusal(),
                    cost,
                    searches,
                    steps,
                    guardrail_reason=gate.reason,
                )

            # Act: one retrieval (always counts a search) + one grounded answer.
            retrieved = retrieve(
                message, tenant_id=state.tenant_id, store=store, embedder=embedder
            )
            searches += 1
            cost += KNOWLEDGE_QUERY_EMBED_COST_USD
            answer = answer_question(message, retrieved, model=model)
            # Bill the LLM only when it was actually invoked (retrieved non-empty);
            # a vector search ran either way. Matches the legacy single-shot cost.
            if retrieved:
                cost += model.cost_per_call_usd
            steps += 1

            # Observe: keep the best grounded answer, then consult the policy.
            best = _prefer(best, answer)
            if self._decide(answer, step):
                break

        return PlannerResult(best if best is not None else _refusal(), cost, searches, steps)


class StubKnowledgePlanner(_LoopPlanner):
    """Deterministic offline loop — forces the :class:`StubChatModel`.

    The no-credentials / CI path. Single-pass by default, so its output mirrors
    the legacy single-shot RAG flow exactly (the stub-equals-legacy golden test).
    """

    def _make_model(self) -> ChatModel:
        return StubChatModel()


class LLMKnowledgePlanner(_LoopPlanner):
    """Real LLM-driven loop — uses the GPT-4o-mini answerer via the env seam."""

    def _make_model(self) -> ChatModel:
        return get_chat_model()


def get_knowledge_planner() -> KnowledgePlanner:
    """Env-driven selector — real LLM loop when ``OPENAI_API_KEY`` set, else stub.

    Same convention as :func:`~agents.knowledge.llm.get_chat_model` /
    ``get_triage_classifier``; the ``REPLACE-ME`` placeholder counts as unset.
    """
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key and key != SECRET_PLACEHOLDER:
        return LLMKnowledgePlanner()
    return StubKnowledgePlanner()
