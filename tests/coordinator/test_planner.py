"""Tests for the Coordinator plan-execute reasoning loop (CM-88, Track B).

The whole loop is exercised offline through the deterministic
:class:`StubCoordinatorPlanner` (no ``OPENAI_API_KEY``), so CI is green with no
credentials. Coverage:

* the env-driven :func:`get_planner` selector (stub vs LLM, REPLACE-ME placeholder);
* deterministic stub trajectory — exact ordered step sequence from ``sub_intents``,
  with shared-specialist de-duplication;
* dynamic termination (step count tracks the input, not a fixed hop count) +
  fallback to the primary intent when no sub-intents were detected;
* the hard ``COORDINATOR_MAX_STEPS`` bound + its env override;
* per-iteration guardrail enforcement — mid-loop trip and trip-on-entry;
* the legal gate — any escalation sub-result routes to ``hitl_review`` and the
  held draft is never auto-sent;
* cost accrual across tool calls and the JSON-serializable sub-result shape.
"""

from __future__ import annotations

import json

import pytest
from agents.coordinator.planner import (
    COORDINATOR_MAX_STEPS,
    COORDINATOR_STEP_COST_USD,
    ROUTE_COORDINATOR_DONE,
    ROUTE_GUARDRAIL_TERMINATED,
    ROUTE_HITL_REVIEW,
    LLMCoordinatorPlanner,
    StubCoordinatorPlanner,
    _invoke_tool,
    _resolve_max_steps,
    get_planner,
)
from agents.orchestrator.state import AgentState, Intent


def _state(**kwargs: object) -> AgentState:
    base: dict[str, object] = {"tenant_id": "t-1", "request_id": "r-1"}
    base.update(kwargs)
    return AgentState(**base)  # type: ignore[arg-type]


# --- get_planner() selector --------------------------------------------------


def test_get_planner_selects_stub_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert isinstance(get_planner(), StubCoordinatorPlanner)


def test_get_planner_treats_placeholder_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "REPLACE-ME")
    assert isinstance(get_planner(), StubCoordinatorPlanner)


def test_get_planner_selects_llm_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # Returning the class doesn't construct the LLM (langchain is lazy-imported
    # only when the policy is made inside run()), so this needs no key/network.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-key")
    assert isinstance(get_planner(), LLMCoordinatorPlanner)


# --- Deterministic stub trajectory -------------------------------------------


def test_stub_runs_sub_intents_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    state = _state(
        raw_message="what's the pet policy and the sink is leaking in 4B",
        intent=Intent.INQUIRY,
        sub_intents=["inquiry", "maintenance"],
    )
    result = StubCoordinatorPlanner().run(state)

    assert [o["tool"] for o in result.sub_results] == [
        "knowledge_agent",
        "maintenance_agent",
    ]
    assert result.steps == 2
    assert result.termination == "satisfied"
    assert result.route == ROUTE_COORDINATOR_DONE


def test_stub_dedups_shared_specialist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two sub-intents mapping to the same tool call it once (maintenance + follow-up)."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    state = _state(
        raw_message="the heater is broken and any update on my last ticket",
        intent=Intent.MAINTENANCE,
        sub_intents=["maintenance", "follow-up"],
    )
    result = StubCoordinatorPlanner().run(state)
    assert [o["tool"] for o in result.sub_results] == ["maintenance_agent"]
    assert result.steps == 1


def test_dynamic_termination_tracks_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """Step count follows the decomposition — not a fixed hop count."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    one = StubCoordinatorPlanner().run(
        _state(raw_message="pet policy?", intent=Intent.INQUIRY, sub_intents=["inquiry"])
    )
    two = StubCoordinatorPlanner().run(
        _state(
            raw_message="pet policy and a leak",
            intent=Intent.INQUIRY,
            sub_intents=["inquiry", "maintenance"],
        )
    )
    assert one.steps == 1
    assert two.steps == 2


def test_empty_sub_intents_falls_back_to_primary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    state = _state(raw_message="how do I book the clubhouse?", intent=Intent.INQUIRY)
    result = StubCoordinatorPlanner().run(state)
    assert [o["tool"] for o in result.sub_results] == ["knowledge_agent"]
    assert result.steps == 1
    assert result.termination == "satisfied"


# --- Bound + env override ----------------------------------------------------


def test_max_steps_bound_truncates_trajectory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    state = _state(
        raw_message="pet policy and a leak",
        intent=Intent.INQUIRY,
        sub_intents=["inquiry", "maintenance"],
    )
    result = StubCoordinatorPlanner(max_steps=1).run(state)
    assert result.steps == 1  # bound stopped it before the 2nd sub-task
    assert result.termination == "bound"
    assert [o["tool"] for o in result.sub_results] == ["knowledge_agent"]


def test_resolve_max_steps_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COORDINATOR_MAX_STEPS", "3")
    assert _resolve_max_steps() == 3


@pytest.mark.parametrize("bad", ["0", "-2", "nope", ""])
def test_resolve_max_steps_rejects_bad_values(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    """A non-positive / non-integer env can never disable the loop bound."""
    monkeypatch.setenv("COORDINATOR_MAX_STEPS", bad)
    assert _resolve_max_steps() == COORDINATOR_MAX_STEPS


# --- Guardrail enforcement ---------------------------------------------------


def test_guardrail_trips_mid_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Stop Rule that trips after the first tool call short-circuits the loop."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # search_count starts at the cap; the first tool call bumps it to 51 > 50,
    # so the 2nd iteration's guardrail-first check trips.
    state = _state(
        raw_message="the heater is broken and I want a manager",
        intent=Intent.MAINTENANCE,
        sub_intents=["maintenance", "escalation"],
        search_count=50,
    )
    result = StubCoordinatorPlanner().run(state)
    assert result.termination == "guardrail"
    assert result.route == ROUTE_GUARDRAIL_TERMINATED
    assert result.guardrail_reason is not None
    # Only the first tool ran before the trip — the escalation tool never fired.
    assert [o["tool"] for o in result.sub_results] == ["maintenance_agent"]


def test_guardrail_tripped_on_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    state = _state(
        raw_message="pet policy and a leak",
        intent=Intent.INQUIRY,
        sub_intents=["inquiry", "maintenance"],
        cost_so_far=999.0,  # already over the $5 cap
    )
    result = StubCoordinatorPlanner().run(state)
    assert result.termination == "guardrail"
    assert result.steps == 0
    assert result.sub_results == []


# --- Legal gate (AC #5) ------------------------------------------------------


def test_legal_escalation_routes_to_hitl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    state = _state(
        raw_message="fix the leak and I'm calling my lawyer about this",
        intent=Intent.MAINTENANCE,
        sub_intents=["maintenance", "escalation"],
    )
    result = StubCoordinatorPlanner().run(state)

    assert result.route == ROUTE_HITL_REVIEW
    assert result.escalation is not None
    assert result.escalation.legal_risk is True
    # Nothing is auto-sent — the held draft exists but no sub-result marks a send.
    assert result.escalation.tenant_draft
    assert result.escalation.status == "pending_review"
    for obs in result.sub_results:
        assert obs["result"].get("sent") is not True


def test_any_escalation_gates_even_without_legal_risk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    state = _state(
        raw_message="the heater is broken and I demand to speak to a manager",
        intent=Intent.MAINTENANCE,
        sub_intents=["maintenance", "escalation"],
    )
    result = StubCoordinatorPlanner().run(state)
    assert result.route == ROUTE_HITL_REVIEW
    assert result.escalation is not None


# --- Cost accrual + sub-result shape -----------------------------------------


def test_cost_accrues_across_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    two = StubCoordinatorPlanner().run(
        _state(
            raw_message="pet policy and a leak",
            intent=Intent.INQUIRY,
            sub_intents=["inquiry", "maintenance"],
        )
    )
    # Each tool call accrues at least the flat per-step cost (cap applies to the
    # whole trajectory, AC #3).
    assert two.cost_usd >= 2 * COORDINATOR_STEP_COST_USD
    assert two.searches == 2


def test_sub_results_are_json_serializable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = StubCoordinatorPlanner().run(
        _state(
            raw_message="pet policy and a leak",
            intent=Intent.INQUIRY,
            sub_intents=["inquiry", "maintenance"],
        )
    )
    for obs in result.sub_results:
        assert set(obs) >= {"tool", "result"}
    json.dumps(result.sub_results)  # survives the Cosmos checkpointer


def test_invoke_vendor_skipped_without_ticket() -> None:
    """vendor_agent with no prior ticket records a skip, never an empty dispatch."""
    state = _state(raw_message="dispatch someone", intent=Intent.MAINTENANCE)
    obs = _invoke_tool("vendor_agent", state, [], "dispatch someone")
    assert obs["tool"] == "vendor_agent"
    assert obs["result"]["status"] == "skipped"
