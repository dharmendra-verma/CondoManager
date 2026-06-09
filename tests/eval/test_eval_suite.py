"""Unified PRD eval-suite gate (CM-39).

Runs every agent's offline eval through ``agents.eval.run_suite`` and asserts
all deterministic PRD gates pass. Tagged ``@pytest.mark.eval`` so the whole eval
layer can be selected with ``pytest -m eval`` (it also runs under plain pytest).
"""

from __future__ import annotations

import pytest
from agents.eval import run_suite

pytestmark = pytest.mark.eval

EXPECTED_AGENTS = {
    "triage",
    "knowledge",
    "knowledge_multihop",
    "maintenance_dedup",
    "vendor",
    "escalation",
}


def test_suite_covers_all_agents_without_errors() -> None:
    report = run_suite(live=False)
    assert {c.name for c in report.cases} == EXPECTED_AGENTS
    errored = [(c.name, c.error) for c in report.cases if c.error is not None]
    assert not errored, f"eval cases errored: {errored}"


def test_all_offline_gates_pass() -> None:
    report = run_suite(live=False)
    failed = [
        (c.name, m.metric, round(m.value, 4), m.comparator, m.target)
        for c in report.cases
        for m in c.metrics
        if m.gated and not m.passed
    ]
    assert not failed, f"failed PRD gates: {failed}\n{report.scorecard()}"
    assert report.passed


def test_triage_accuracy_reported_but_not_gated_offline() -> None:
    report = run_suite(live=False)
    triage = next(c for c in report.cases if c.name == "triage")
    metric = triage.metrics[0]
    assert metric.metric == "triage.intent_accuracy"
    assert metric.gated is False  # the >90% gate is live-model-only
    assert 0.0 <= metric.value <= 1.0


def test_scorecard_renders() -> None:
    sc = run_suite(live=False).scorecard()
    assert "PRD eval scorecard" in sc
    assert "OVERALL" in sc
