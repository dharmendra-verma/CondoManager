"""Offline tests for the policy Q&A Knowledge-RAG eval (CM-91).

Runs the real ``retrieve`` + ``answer_question`` path over the deterministic
``LexicalRetriever`` + ``StubChatModel`` doubles (no ``OPENAI_API_KEY``), so CI is
green with no key. Coverage:

* the generated seed is well-formed (count, multi-hop flag, required keys);
* offline self-service clears the deterministic gate and answer-quality is
  reported (low, since the stub only echoes);
* per-policy / per-difficulty breakdowns + the multi-hop slice are populated;
* refusal accounting — a question whose gold chunk is missing refuses and drags
  self-service + answer-quality down;
* the `policy_qa` case is wired into the CM-39 eval suite and passes offline.
"""

from __future__ import annotations

from typing import Any

import pytest
from agents.eval import datasets
from agents.eval.lexical import LexicalRetriever, kb_chunk
from agents.eval.policy_qa import (
    SELF_SERVICE_TARGET,
    PolicyQAReport,
    run_policy_qa_eval,
    token_f1,
)
from agents.knowledge.llm import StubChatModel

# Part of the offline PRD eval gate (select with `pytest -m eval`).
pytestmark = pytest.mark.eval


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def _seed() -> list[dict[str, Any]]:
    return datasets.load_jsonl(datasets.POLICY_QA_SEED)


def _full_kb_retriever(rows: list[dict[str, Any]]) -> LexicalRetriever:
    kb = [
        kb_chunk(f"{r['outputs']['gold_doc_id']}-{i}", str(r["outputs"]["gold_chunk_text"]))
        for i, r in enumerate(rows)
        if r["outputs"]["answerable"]
    ]
    return LexicalRetriever(kb)


# --- seed integrity ----------------------------------------------------------


def test_seed_is_well_formed() -> None:
    rows = _seed()
    assert len(rows) >= 60
    assert sum(r["outputs"]["multi_hop"] for r in rows) == 2  # the 2 CROSS-* rows
    for r in rows:
        assert r["inputs"]["message"]
        assert {"answerable", "expected_answer", "gold_doc_id", "difficulty"} <= set(r["outputs"])


# --- offline scoring ---------------------------------------------------------


def test_offline_self_service_clears_gate_and_quality_reported() -> None:
    rows = _seed()
    ret = _full_kb_retriever(rows)
    report = run_policy_qa_eval(rows, store=ret, embedder=ret, model=StubChatModel())

    assert isinstance(report, PolicyQAReport)
    assert report.answerable == len(rows)
    assert report.self_service >= SELF_SERVICE_TARGET  # gate clears (every gold chunk present)
    assert report.hallucination == 0.0  # no unanswerable rows in this set
    assert 0.0 < report.answer_quality < 1.0  # reported: stub echoes -> partial overlap
    assert report.passed_offline


def test_breakdowns_are_populated() -> None:
    rows = _seed()
    ret = _full_kb_retriever(rows)
    report = run_policy_qa_eval(rows, store=ret, embedder=ret, model=StubChatModel())

    # 4 single-policy slugs + the cross-policy "multiple".
    assert {"pet-policy", "ev-charging-policy", "clubhouse-booking-policy"} <= set(
        report.per_policy
    )
    assert {"easy", "medium", "hard"} <= set(report.per_difficulty)
    assert report.multi_hop.n == 2
    # every slice's counts sum back to the answerable total
    assert sum(g.n for g in report.per_policy.values()) == report.answerable


# --- refusal accounting ------------------------------------------------------


def test_refusal_on_answerable_drags_score_down() -> None:
    rows = _seed()[:5]
    # An empty KB -> retrieve returns nothing -> answer_question refuses every row
    # (deterministic). Refusals on answerable questions must zero the score.
    empty = LexicalRetriever([])
    report = run_policy_qa_eval(rows, store=empty, embedder=empty, model=StubChatModel())

    assert report.answerable == 5
    assert report.answered == 0  # all refused
    assert report.self_service == 0.0  # refusals fully accounted against self-service
    assert report.answer_quality == 0.0  # ...and against answer-quality
    assert not report.passed_offline  # the gate fails when everything refuses


# --- token_f1 unit -----------------------------------------------------------


def test_token_f1_bounds() -> None:
    assert token_f1("quiet hours ten pm seven am", "quiet hours ten pm seven am") == 1.0
    assert token_f1("banquet hall booking deposit", "swimming pool gym timings") == 0.0
    assert 0.0 < token_f1("pets allowed with deposit", "pets allowed maximum two per unit") < 1.0


# --- suite wiring ------------------------------------------------------------


def test_policy_qa_case_in_suite_passes_offline() -> None:
    from agents.eval.suite import run_suite

    report = run_suite(live=False)
    case = next((c for c in report.cases if c.name == "policy_qa"), None)
    assert case is not None
    assert case.error is None
    assert case.passed
