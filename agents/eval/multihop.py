"""Agentic-RAG trajectory eval for the iterative Knowledge agent (CM-85).

The CM-84 loop has a non-deterministic trajectory the golden-label classification
evals can't measure. This module scores it two ways, mirroring the CM-30 triage
split (deterministic offline gate + live LLM-judge diagnostic):

* **hallucination** — unanswerable questions the iterative agent answered anyway
  (the gated CI metric; deterministic with the stub planner).
* **resolution lift** — fraction of single-shot refusals the iterative agent
  recovered (the headline multi-hop value; ~0 offline by construction since the
  stub reformulation derives the same lexical terms, non-zero on the live run).
* **judge score** — a live-only LLM-judge of answer groundedness/correctness vs
  the reference answer (diagnostic, never a hard gate).

Offline (no key) the loop runs against a deterministic ``LexicalRetriever`` over
a KB built from the seed chunks, injected via the planner's DI seam — so the real
loop (retrieve -> accumulate -> decide) is exercised with no network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agents.eval import datasets
from agents.eval.lexical import LexicalRetriever, kb_chunk


@dataclass(frozen=True)
class TrajectoryRow:
    """One scored row: the iterative trajectory + the single-shot baseline."""

    message: str
    answerable: bool
    iterative_refused: bool
    single_shot_refused: bool
    steps: int
    searches: int
    reformulations: int
    termination: str


@dataclass
class MultiHopReport:
    """Aggregate trajectory-eval outcome over the multi-hop seed."""

    rows: list[TrajectoryRow] = field(default_factory=list)
    judge_score: float | None = None  # None offline (no LLM judge)

    @property
    def answerable(self) -> int:
        return sum(1 for r in self.rows if r.answerable)

    @property
    def iterative_self_service(self) -> float:
        ans = self.answerable
        if not ans:
            return 0.0
        return sum(1 for r in self.rows if r.answerable and not r.iterative_refused) / ans

    @property
    def hallucination_rate(self) -> float:
        if not self.rows:
            return 0.0
        bad = sum(1 for r in self.rows if not r.answerable and not r.iterative_refused)
        return bad / len(self.rows)

    @property
    def resolution_lift(self) -> float:
        """Fraction of single-shot refusals the iterative agent recovered."""
        refused = [r for r in self.rows if r.single_shot_refused]
        if not refused:
            return 0.0
        recovered = sum(
            1 for r in refused if r.answerable and not r.iterative_refused
        )
        return recovered / len(refused)


def _build_kb(rows: list[dict[str, Any]]) -> list[Any]:
    """Build the eval KB from every answerable row's chunks."""
    kb: list[Any] = []
    for row in rows:
        if not row["outputs"]["answerable"]:
            continue
        for chunk in row["outputs"]["chunks"]:
            kb.append(kb_chunk(str(chunk["doc_id"]), str(chunk["text"])))
    return kb


def run_multihop_eval(*, live: bool) -> MultiHopReport:
    """Run the iterative + single-shot agents over the multi-hop seed.

    ``live=False`` (CI) uses the deterministic ``StubKnowledgePlanner`` +
    ``StubChatModel`` over a ``LexicalRetriever``; ``live=True`` uses the real
    LLM loop and an LLM judge (both need ``OPENAI_API_KEY``).
    """
    from agents.knowledge.llm import StubChatModel, get_chat_model  # noqa: PLC0415
    from agents.knowledge.planner import (  # noqa: PLC0415
        LLMKnowledgePlanner,
        StubKnowledgePlanner,
    )
    from agents.knowledge.rag import answer_question  # noqa: PLC0415
    from agents.knowledge.retrieval import retrieve  # noqa: PLC0415
    from agents.orchestrator.state import AgentState  # noqa: PLC0415

    rows = datasets.load_jsonl(datasets.KNOWLEDGE_MULTIHOP_SEED)
    retriever = LexicalRetriever(_build_kb(rows))
    planner = LLMKnowledgePlanner() if live else StubKnowledgePlanner()
    single_model = get_chat_model() if live else StubChatModel()
    judge = LLMMultiHopJudge() if live else None

    out: list[TrajectoryRow] = []
    judge_scores: list[float] = []
    for row in rows:
        message = str(row["inputs"]["message"])
        answerable = bool(row["outputs"]["answerable"])
        state = AgentState(tenant_id="t-eval", request_id="r-eval")

        result = planner.run(message, state=state, store=retriever, embedder=retriever)
        retrieved = retrieve(message, tenant_id="t-eval", store=retriever, embedder=retriever)
        single = answer_question(message, retrieved, model=single_model)

        if judge is not None and answerable and not result.answer.refused:
            reference = str(row["outputs"].get("reference_answer", ""))
            judge_scores.append(judge.score(message, result.answer.answer, reference))

        out.append(
            TrajectoryRow(
                message=message,
                answerable=answerable,
                iterative_refused=result.answer.refused,
                single_shot_refused=single.refused,
                steps=result.steps,
                searches=result.searches,
                reformulations=result.reformulations,
                termination=result.termination,
            )
        )

    judge_score = sum(judge_scores) / len(judge_scores) if judge_scores else None
    return MultiHopReport(rows=out, judge_score=judge_score)


# --- live-only LLM judge ------------------------------------------------------


class LLMMultiHopJudge:
    """GPT-4o-mini judge of answer groundedness + correctness vs a reference.

    Live-only (constructed solely when ``OPENAI_API_KEY`` is set). Returns a
    0..1 score; kept a diagnostic, never a hard CI gate, because LLM-judging is
    fuzzy.
    """

    def __init__(self, model: str = "gpt-4o-mini", *, temperature: float = 0.0) -> None:
        from langchain_openai import ChatOpenAI  # noqa: PLC0415  (lazy by design)
        from pydantic import BaseModel, Field  # noqa: PLC0415

        class _Verdict(BaseModel):
            grounded: bool
            correct: bool
            score: float = Field(ge=0.0, le=1.0)

        self._verdict_cls = _Verdict
        chat_cls: Any = ChatOpenAI
        self._llm: Any = chat_cls(
            model=model, temperature=temperature
        ).with_structured_output(_Verdict)

    def score(self, question: str, answer: str, reference: str) -> float:
        from langchain_core.messages import HumanMessage, SystemMessage  # noqa: PLC0415

        system = (
            "You are grading a condominium knowledge assistant. Given the question, "
            "the assistant's answer, and a reference answer, judge whether the answer "
            "is grounded (no invented facts) and correct (matches the reference). "
            "Return grounded, correct, and an overall score from 0.0 to 1.0."
        )
        human = f"Question: {question}\n\nAnswer: {answer}\n\nReference: {reference}"
        result = self._llm.invoke(
            [SystemMessage(content=system), HumanMessage(content=human)]
        )
        return float(result.score)
