"""Tests for the Coordinator trajectory / sub-task-coverage eval (CM-90, Track B).

The whole eval runs offline through the deterministic ``StubCoordinatorPlanner``
+ the template synthesizer (no ``OPENAI_API_KEY``), so CI is green with no key:

* the golden set scores **coverage == 100%** on every stub trajectory (AC #2);
* **tool-selection correctness** — the stub fires exactly the expected tools (AC #3);
* coverage-scorer unit cases — partial coverage, ``skipped`` excluded, a refused
  leg still counts as addressed, held escalation counts, exact-match semantics.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from agents.coordinator.eval import (
    COVERAGE_TARGET,
    addressed_tools,
    run_coverage_eval,
    subtask_coverage,
    tool_selection_exact,
)
from agents.coordinator.planner import CoordinatorResult, StubCoordinatorPlanner
from agents.coordinator.synthesis import SynthesisResult, synthesize
from agents.orchestrator.state import AgentState

_SEED = Path(__file__).resolve().parents[2] / "tests" / "eval" / "coordinator_seed.jsonl"


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the no-credentials path so the specialist tools stay offline."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def _load_seed() -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    with _SEED.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                examples.append(json.loads(line))
    return examples


def _stub_runner(example: dict[str, Any]) -> tuple[CoordinatorResult, SynthesisResult]:
    """Run one example through the stub planner + template synthesizer (offline)."""
    inp = example["inputs"]
    state = AgentState(
        tenant_id=inp.get("tenant_id", "t-eval"),
        request_id="r-eval",
        raw_message=inp["message"],
        sub_intents=inp.get("sub_intents", []),
    )
    result = StubCoordinatorPlanner().run(state)
    return result, synthesize(result.sub_results)


# --- golden-set gate (AC #2 / #3) --------------------------------------------


def test_seed_file_is_non_trivial() -> None:
    examples = _load_seed()
    assert len(examples) >= 12
    # Compound coverage: at least some 3-tool trajectories exist.
    assert any(len(e["outputs"]["expected_tools"]) >= 3 for e in examples)


def test_stub_coverage_is_100_percent() -> None:
    report = run_coverage_eval(_stub_runner, _load_seed())
    assert report.n >= 12
    assert report.mean_coverage == COVERAGE_TARGET  # every sub-task addressed
    assert report.passed
    assert report.mismatches() == []


def test_stub_tool_selection_is_exact() -> None:
    """Every stub trajectory fires exactly the golden-labelled tool set."""
    for ex in _load_seed():
        result, _ = _stub_runner(ex)
        assert tool_selection_exact(result, ex["outputs"]["expected_tools"]), ex["inputs"][
            "message"
        ]


# --- scorer unit cases -------------------------------------------------------


def _res(subs: list[dict[str, Any]]) -> CoordinatorResult:
    return CoordinatorResult(sub_results=subs)


def _syn(tools: list[str], *, reply: str = "reply", held: bool = False) -> SynthesisResult:
    parts = [{"tool": t, "status": "x", "codes": []} for t in tools]
    return SynthesisResult(reply=reply, parts=parts, held_for_review=held)


def test_partial_coverage_when_a_tool_is_missing() -> None:
    result = _res(
        [{"tool": "maintenance_agent", "result": {"output": {"status": "ticket_created"}}}]
    )
    synthesis = _syn(["maintenance_agent"])
    assert subtask_coverage(result, synthesis, ["maintenance_agent", "knowledge_agent"]) == 0.5


def test_skipped_tool_does_not_count_as_addressed() -> None:
    result = _res([{"tool": "vendor_agent", "result": {"status": "skipped"}}])
    synthesis = _syn(["vendor_agent"])
    assert addressed_tools(result, synthesis) == set()
    assert subtask_coverage(result, synthesis, ["vendor_agent"]) == 0.0


def test_refused_leg_still_counts_as_addressed() -> None:
    """A refused knowledge answer is surfaced (not dropped) -> addressed."""
    result = _res([{"tool": "knowledge_agent", "result": {"status": "refused", "refused": True}}])
    synthesis = _syn(["knowledge_agent"])
    assert subtask_coverage(result, synthesis, ["knowledge_agent"]) == 1.0


def test_held_escalation_counts_as_addressed() -> None:
    result = _res(
        [
            {
                "tool": "escalation_agent",
                "result": {"record_id": "e", "status": "pending_review", "legal_risk": True},
            }
        ]
    )
    synthesis = _syn(["escalation_agent"], reply="held for manager review", held=True)
    assert subtask_coverage(result, synthesis, ["escalation_agent"]) == 1.0


def test_tool_selection_exact_rejects_extras_and_misses() -> None:
    result = _res(
        [
            {"tool": "maintenance_agent", "result": {"output": {"status": "ticket_created"}}},
            {"tool": "knowledge_agent", "result": {"status": "answered"}},
        ]
    )
    assert tool_selection_exact(result, ["maintenance_agent", "knowledge_agent"])
    assert not tool_selection_exact(result, ["maintenance_agent"])  # missed knowledge
    assert not tool_selection_exact(
        result, ["maintenance_agent", "knowledge_agent", "vendor_agent"]
    )


def test_empty_expected_is_full_coverage() -> None:
    assert subtask_coverage(_res([]), _syn([]), []) == 1.0
