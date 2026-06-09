"""Offline trajectory eval for the CM-84/85 iterative Knowledge agent (CM-85).

Deterministic (stub planner, no key): asserts the multi-hop trajectory **shape**
— hop count, query-dedup, termination reason — and the hallucination gate over
the multi-hop seed. Resolution lift + the LLM judge are live-only diagnostics and
are exercised by the suite, not gated here.
"""

from __future__ import annotations

from agents.eval.multihop import run_multihop_eval


def test_offline_multihop_trajectory_shape_and_hallucination() -> None:
    report = run_multihop_eval(live=False)

    # Dataset sanity: enough answerable rows + at least one unanswerable.
    assert report.answerable >= 10
    assert any(not r.answerable for r in report.rows)

    answerable = [r for r in report.rows if r.answerable]
    unanswerable = [r for r in report.rows if not r.answerable]

    # Every answerable row runs the deterministic stub 2-hop and answers:
    for r in answerable:
        assert r.termination == "answer", r.message
        assert not r.iterative_refused, r.message
        assert r.steps == 2, r.message  # fixed 2-hop (reformulate -> answer)
        assert r.reformulations == 1, r.message
        # query-dedup: one retrieval per step, the 2nd hop a distinct query.
        assert r.searches == r.steps == 2, r.message

    # Unanswerable rows refuse -> no hallucination.
    for r in unanswerable:
        assert r.iterative_refused, r.message

    assert report.hallucination_rate < 0.01
    assert report.iterative_self_service == 1.0
    # Offline lift is ~0 by construction (stub reformulation = same lexical terms).
    assert report.resolution_lift == 0.0
    # No LLM judge offline.
    assert report.judge_score is None


def test_suite_registers_multihop_case_with_correct_gating() -> None:
    from agents.eval.suite import run_suite

    report = run_suite(live=False)
    case = next(c for c in report.cases if c.name == "knowledge_multihop")
    assert case.error is None
    by_name = {m.metric: m for m in case.metrics}
    # Hallucination is the CI gate; lift + judge are reported diagnostics.
    assert by_name["knowledge_multihop.hallucination"].gated is True
    assert by_name["knowledge_multihop.resolution_lift"].gated is False
    assert by_name["knowledge_multihop.judge_score"].gated is False
    assert case.passed
