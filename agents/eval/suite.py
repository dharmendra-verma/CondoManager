"""Unified offline eval suite — one PRD scorecard over all agents (CM-39).

Each agent already ships a golden dataset + a scorer (CM-30/31/32/33/35). This
module *registers* them as :class:`EvalCase`s and runs them into a single
:class:`SuiteReport` so CI (and operators via ``run-eval-suite.py``) can assert
every PRD gate in one place. Agent SDKs are lazy-imported per case so loading
the suite is cheap and one agent's heavy import doesn't tax the others.

Offline (no ``OPENAI_API_KEY``) every case uses its deterministic stand-in —
the same ones the per-agent eval tests use — so the suite is reproducible in CI
with no network. The Triage *accuracy gate* (>90%) only applies to the real
model, so offline it is **reported, not gated** (the heuristic isn't expected
to clear it); pass ``live=True`` with a key configured to gate it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from agents.eval import datasets


@dataclass(frozen=True)
class MetricResult:
    """One measured PRD metric + its gate verdict."""

    metric: str
    value: float
    target: float | None
    comparator: str  # one of ">", ">=", "<", "<=", "=="
    gated: bool

    @property
    def passed(self) -> bool:
        if not self.gated or self.target is None:
            return True
        return _compare(self.value, self.target, self.comparator)


@dataclass
class CaseResult:
    """All metrics for one agent's eval (or an ``error`` if it failed to run)."""

    name: str
    metrics: list[MetricResult] = field(default_factory=list)
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and all(m.passed for m in self.metrics)


@dataclass
class SuiteReport:
    """The consolidated PRD scorecard."""

    cases: list[CaseResult]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.cases)

    def scorecard(self) -> str:
        lines = ["PRD eval scorecard", "=" * 60]
        for case in self.cases:
            if case.error is not None:
                lines.append(f"[ERROR] {case.name}: {case.error}")
                continue
            for m in case.metrics:
                gate = (
                    f"{m.comparator} {m.target}" if m.target is not None else "(report)"
                )
                status = "PASS" if m.passed else ("--" if not m.gated else "FAIL")
                lines.append(f"  [{status:>4}] {m.metric:<32} {m.value:.4f}  {gate}")
        lines.append("=" * 60)
        lines.append(f"OVERALL: {'PASS' if self.passed else 'FAIL'}")
        return "\n".join(lines)


def _compare(value: float, target: float, comparator: str) -> bool:
    if comparator == ">":
        return value > target
    if comparator == ">=":
        return value >= target
    if comparator == "<":
        return value < target
    if comparator == "<=":
        return value <= target
    if comparator == "==":
        return value == target
    raise ValueError(f"unknown comparator: {comparator!r}")


def _fixed_wednesday() -> datetime:
    """A deterministic weekday (so weekday-only vendors are in play)."""
    base = datetime(2026, 5, 25, 10, 0, tzinfo=UTC)
    return base + timedelta(days=(2 - base.weekday()) % 7)


# --- per-agent cases ---------------------------------------------------------


def _eval_triage(live: bool) -> CaseResult:
    from agents.orchestrator.eval import run_eval  # noqa: PLC0415

    # Offline forces the deterministic heuristic (no network, no key dependence);
    # live uses the env-selected classifier (real model when OPENAI_API_KEY set).
    if live:
        from agents.orchestrator.triage import get_triage_classifier  # noqa: PLC0415

        classifier = get_triage_classifier()
    else:
        from agents.orchestrator.triage import HeuristicTriageClassifier  # noqa: PLC0415

        classifier = HeuristicTriageClassifier()

    examples = datasets.load_jsonl(datasets.TRIAGE_SEED)
    report = run_eval(classifier, examples)
    # Gate the >90% intent-accuracy target only against the real model (live);
    # the offline heuristic is reported, not gated.
    return CaseResult(
        name="triage",
        metrics=[
            MetricResult("triage.intent_accuracy", report.intent_accuracy, 0.90, ">", gated=live),
        ],
    )


def _eval_knowledge(live: bool) -> CaseResult:
    from agents.eval.lexical import LexicalRetriever, kb_chunk  # noqa: PLC0415
    from agents.knowledge.llm import StubChatModel  # noqa: PLC0415
    from agents.knowledge.rag import answer_question  # noqa: PLC0415
    from agents.knowledge.retrieval import retrieve  # noqa: PLC0415

    rows = datasets.load_jsonl(datasets.KNOWLEDGE_SEED)
    kb = [
        kb_chunk(str(r["outputs"]["gold_doc_id"]), str(r["outputs"]["gold_chunk_text"]))
        for r in rows
        if r["outputs"]["answerable"]
    ]
    retriever = LexicalRetriever(kb)
    model = StubChatModel()

    answerable = answered = hallucinations = 0
    for row in rows:
        message = str(row["inputs"]["message"])
        retrieved = retrieve(message, tenant_id="t-eval", store=retriever, embedder=retriever)
        answer = answer_question(message, retrieved, model=model)
        if row["outputs"]["answerable"]:
            answerable += 1
            if not answer.refused:
                answered += 1
        elif not answer.refused:
            hallucinations += 1

    self_service = answered / answerable if answerable else 0.0
    hallucination_rate = hallucinations / len(rows) if rows else 0.0
    return CaseResult(
        name="knowledge",
        metrics=[
            MetricResult("knowledge.self_service", self_service, 0.25, ">=", gated=True),
            MetricResult("knowledge.hallucination", hallucination_rate, 0.01, "<", gated=True),
        ],
    )


def _eval_knowledge_multihop(live: bool) -> CaseResult:
    from agents.eval.multihop import run_multihop_eval  # noqa: PLC0415

    report = run_multihop_eval(live=live)
    # Hallucination is the deterministic CI gate. Resolution lift + the LLM-judge
    # score are reported diagnostics (~0 / absent offline; meaningful only live).
    return CaseResult(
        name="knowledge_multihop",
        metrics=[
            MetricResult(
                "knowledge_multihop.hallucination",
                report.hallucination_rate,
                0.01,
                "<",
                gated=True,
            ),
            MetricResult(
                "knowledge_multihop.resolution_lift",
                report.resolution_lift,
                None,
                ">",
                gated=False,
            ),
            MetricResult(
                "knowledge_multihop.judge_score",
                report.judge_score if report.judge_score is not None else 0.0,
                None,
                ">",
                gated=False,
            ),
        ],
    )


def _eval_policy_qa(live: bool) -> CaseResult:
    from agents.eval.lexical import LexicalRetriever, kb_chunk  # noqa: PLC0415
    from agents.eval.policy_qa import (  # noqa: PLC0415
        HALLUCINATION_TARGET,
        SELF_SERVICE_TARGET,
        run_policy_qa_eval,
    )
    from agents.knowledge.llm import StubChatModel  # noqa: PLC0415

    # CM-91: the richer policy Q&A set (per-policy/difficulty labels). Like the
    # knowledge case, the deterministic lexical doubles run both offline and live
    # (a real vector-store run is the dedicated infra/scripts/eval-policy-qa.py).
    rows = datasets.load_jsonl(datasets.POLICY_QA_SEED)
    kb = [
        kb_chunk(f"{r['outputs']['gold_doc_id']}-{i}", str(r["outputs"]["gold_chunk_text"]))
        for i, r in enumerate(rows)
        if r["outputs"]["answerable"]
    ]
    retriever = LexicalRetriever(kb)
    report = run_policy_qa_eval(rows, store=retriever, embedder=retriever, model=StubChatModel())
    # Self-service + hallucination are the deterministic gates; answer-quality is
    # reported (the StubChatModel only echoes, so it's gated live by the CLI).
    return CaseResult(
        name="policy_qa",
        metrics=[
            MetricResult(
                "policy_qa.self_service",
                report.self_service,
                SELF_SERVICE_TARGET,
                ">=",
                gated=True,
            ),
            MetricResult(
                "policy_qa.hallucination",
                report.hallucination,
                HALLUCINATION_TARGET,
                "<",
                gated=True,
            ),
            MetricResult("policy_qa.answer_quality", report.answer_quality, None, ">", gated=False),
        ],
    )


def _eval_maintenance_dedup(live: bool) -> CaseResult:
    from agents.maintenance.dedup import is_duplicate_pair  # noqa: PLC0415

    rows = datasets.load_jsonl(datasets.DEDUP_SEED)
    tp = fp = 0
    for row in rows:
        existing, candidate = row["existing"], row["candidate"]
        predicted = is_duplicate_pair(
            existing_unit=existing["unit"],
            existing_issue=existing["issue"],
            candidate_unit=candidate["unit"],
            candidate_issue=candidate["issue"],
            age_days=existing["created_days_ago"],
        )
        if predicted and bool(row["is_duplicate"]):
            tp += 1
        elif predicted and not bool(row["is_duplicate"]):
            fp += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    return CaseResult(
        name="maintenance_dedup",
        metrics=[
            MetricResult("maintenance.dedup_precision", precision, 0.95, ">", gated=True),
        ],
    )


def _eval_vendor(live: bool) -> CaseResult:
    from agents.vendor.matching import match_vendors  # noqa: PLC0415
    from agents.vendor.seed import seed_vendors  # noqa: PLC0415

    rows = datasets.load_jsonl(datasets.VENDOR_SEED)
    vendors = seed_vendors()
    when = _fixed_wednesday()
    correct = 0
    for row in rows:
        matches = match_vendors(row["category"], when, vendors)
        if matches and matches[0].vendor.id == row["expected_vendor_id"]:
            correct += 1
    accuracy = correct / len(rows) if rows else 0.0
    return CaseResult(
        name="vendor",
        metrics=[
            MetricResult("vendor.match_accuracy", accuracy, 0.90, ">=", gated=True),
        ],
    )


def _eval_escalation(live: bool) -> CaseResult:
    from agents.orchestrator.escalation import HeuristicEscalationClassifier  # noqa: PLC0415

    rows = datasets.load_jsonl(datasets.ESCALATION_SEED)
    clf = HeuristicEscalationClassifier()
    legal = [r for r in rows if r["outputs"]["legal_risk"] is True]
    hits = sum(clf.classify(r["inputs"]["message"], []).legal_risk for r in legal)
    recall = hits / len(legal) if legal else 0.0
    return CaseResult(
        name="escalation",
        metrics=[
            MetricResult("escalation.legal_recall", recall, 1.0, ">=", gated=True),
        ],
    )


#: Registry — name → case function. Order is the scorecard order.
CASES: list[tuple[str, Callable[[bool], CaseResult]]] = [
    ("triage", _eval_triage),
    ("knowledge", _eval_knowledge),
    ("knowledge_multihop", _eval_knowledge_multihop),
    ("policy_qa", _eval_policy_qa),
    ("maintenance_dedup", _eval_maintenance_dedup),
    ("vendor", _eval_vendor),
    ("escalation", _eval_escalation),
]


def run_suite(*, live: bool = False) -> SuiteReport:
    """Run every registered agent eval into one :class:`SuiteReport`.

    A case that raises is captured as a :class:`CaseResult` with an ``error``
    (not silently dropped) so the scorecard shows it as a failure.
    """
    results: list[CaseResult] = []
    for name, fn in CASES:
        try:
            results.append(fn(live))
        except Exception as exc:  # noqa: BLE001 - one bad case must not hide the rest
            results.append(CaseResult(name=name, error=str(exc)))
    return SuiteReport(cases=results)
