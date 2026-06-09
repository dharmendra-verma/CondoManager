"""Policy Q&A Knowledge-RAG eval (CM-91).

Jira: CM-91  | Epic: Knowledge / RAG eval

Scores the Knowledge agent's grounded answers over the owner's 69-row policy Q&A
golden set (``tests/eval/policy_qa_seed.jsonl``, built from the CSV by
``infra/scripts/build-policy-qa-seed.py``). Extends the existing
``agents/eval/suite.py::_eval_knowledge`` machinery — same ``retrieve`` +
``answer_question`` path, same deterministic ``LexicalRetriever`` + ``StubChatModel``
offline doubles — and adds:

* **answer quality** — mean token-F1 of the produced answer vs ``expected_answer``
  (a refusal scores 0, so refusals on answerable questions count against it —
  the CM-74/CM-78 confidence-threshold interaction);
* **self-service** — % of answerable questions actually answered (not refused);
* **per-policy** and **per-difficulty** breakdowns, plus a **multi-hop** slice.

Pure scoring over an injected ``(store, embedder, model)`` seam — the offline
test injects the lexical doubles (no key), the operator CLI injects the real
planner. Following the CM-30/CM-39/CM-90 split, the deterministic numbers
(self-service / hallucination) gate offline; **answer quality is reported
offline and gated live** (the ``StubChatModel`` only echoes a sentence, so a high
quality bar can't hold without a real model).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agents.knowledge.rag import answer_question
from agents.knowledge.retrieval import retrieve, significant_terms

if TYPE_CHECKING:  # typing-only protocols (structural — LexicalRetriever satisfies both)
    from agents.knowledge.embeddings import Embedder
    from agents.knowledge.llm import ChatModel
    from agents.knowledge.retrieval import SearchStore

#: Offline deterministic gate — answerable questions the lexical path answers.
SELF_SERVICE_TARGET: float = 0.75
#: Offline deterministic gate — answers produced for an UNanswerable question.
HALLUCINATION_TARGET: float = 0.01
#: Live-only gate (reported offline) — mean token-F1 of answer vs expected_answer.
ANSWER_QUALITY_TARGET: float = 0.50


def token_f1(produced: str, expected: str) -> float:
    """Token-overlap F1 between two texts over their significant terms (0.0–1.0)."""
    p = set(significant_terms(produced, max_terms=64))
    e = set(significant_terms(expected, max_terms=64))
    overlap = len(p & e)
    if not overlap:
        return 0.0
    precision = overlap / len(p)
    recall = overlap / len(e)
    return 2 * precision * recall / (precision + recall)


@dataclass
class GroupScore:
    """Aggregate for one breakdown slice (a policy, difficulty, or multi-hop)."""

    n: int = 0
    answered: int = 0
    quality_sum: float = 0.0

    @property
    def self_service(self) -> float:
        return self.answered / self.n if self.n else 0.0

    @property
    def answer_quality(self) -> float:
        # Mean over ALL answerable rows in the slice — a refusal contributes 0.
        return self.quality_sum / self.n if self.n else 0.0


@dataclass
class PolicyQAReport:
    """The policy-QA scorecard: overall + per-policy / per-difficulty / multi-hop."""

    n: int
    answerable: int
    answered: int
    hallucinations: int
    quality_sum: float
    per_policy: dict[str, GroupScore] = field(default_factory=dict)
    per_difficulty: dict[str, GroupScore] = field(default_factory=dict)
    multi_hop: GroupScore = field(default_factory=GroupScore)

    @property
    def self_service(self) -> float:
        return self.answered / self.answerable if self.answerable else 0.0

    @property
    def answer_quality(self) -> float:
        return self.quality_sum / self.answerable if self.answerable else 0.0

    @property
    def hallucination(self) -> float:
        return self.hallucinations / self.n if self.n else 0.0

    @property
    def passed_offline(self) -> bool:
        """Deterministic gate — self-service + hallucination (not answer-quality)."""
        return (
            self.self_service >= SELF_SERVICE_TARGET and self.hallucination < HALLUCINATION_TARGET
        )


def run_policy_qa_eval(
    rows: Iterable[dict[str, Any]],
    *,
    store: SearchStore,
    embedder: Embedder,
    model: ChatModel,
) -> PolicyQAReport:
    """Run the Knowledge retrieve→answer path over ``rows`` and score them.

    Each row is the seed shape (see ``infra/scripts/build-policy-qa-seed.py``):
    ``inputs{message, policy, category}`` + ``outputs{answerable, expected_answer,
    gold_doc_id, difficulty, multi_hop}``.
    """
    rows = list(rows)
    report = PolicyQAReport(n=0, answerable=0, answered=0, hallucinations=0, quality_sum=0.0)

    for row in rows:
        message = str(row["inputs"]["message"])
        out = row["outputs"]
        answerable = bool(out["answerable"])
        retrieved = retrieve(message, tenant_id="t-eval", store=store, embedder=embedder)
        answer = answer_question(message, retrieved, model=model)

        report.n += 1
        if not answerable:
            if not answer.refused:
                report.hallucinations += 1
            continue

        # Answerable: refusal counts against self-service AND answer-quality (0).
        quality = (
            token_f1(answer.answer, str(out["expected_answer"])) if not answer.refused else 0.0
        )
        report.answerable += 1
        report.quality_sum += quality
        if not answer.refused:
            report.answered += 1

        policy = str(out.get("gold_doc_id", "unknown"))
        difficulty = str(out.get("difficulty", "unknown")) or "unknown"
        _accumulate(report.per_policy, policy, answer.refused, quality)
        _accumulate(report.per_difficulty, difficulty, answer.refused, quality)
        if bool(out.get("multi_hop", False)):
            _bump(report.multi_hop, answer.refused, quality)

    return report


def _accumulate(groups: dict[str, GroupScore], key: str, refused: bool, quality: float) -> None:
    _bump(groups.setdefault(key, GroupScore()), refused, quality)


def _bump(group: GroupScore, refused: bool, quality: float) -> None:
    group.n += 1
    group.quality_sum += quality
    if not refused:
        group.answered += 1
