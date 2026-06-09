"""AgentState model + enum tests."""

from __future__ import annotations

import pytest
from agents.orchestrator import AgentState, Channel, Intent, Tone, Urgency
from pydantic import ValidationError


def test_required_fields() -> None:
    s = AgentState(tenant_id="t-1", request_id="r-1")
    assert s.tenant_id == "t-1"
    assert s.request_id == "r-1"
    # Defaults match the CM-26 Stop-Rule starting point.
    assert s.cost_so_far == 0.0
    assert s.search_count == 0


def test_defaults_for_unset_fields() -> None:
    s = AgentState(tenant_id="t-1", request_id="r-1")
    assert s.channel == Channel.UNKNOWN
    assert s.raw_message == ""
    # CM-29: `normalized` tightened from dict[str, Any] (default {}) to
    # NormalizedMessage | None (default None). Channel adapters set it
    # at the orchestrator entry — see agents/channels/web.py.
    assert s.normalized is None
    assert s.intent is None
    assert s.urgency is None
    assert s.tone is None
    assert s.history == []
    assert s.routes == []
    assert s.output is None
    # CM-32: escalation record defaults to None until the escalation node runs.
    assert s.escalation is None


def test_enum_validation() -> None:
    s = AgentState(
        tenant_id="t-1",
        request_id="r-1",
        channel=Channel.WEB,
        intent=Intent.MAINTENANCE,
        urgency=Urgency.EMERGENCY,
        tone=Tone.URGENT,
    )
    assert s.intent == Intent.MAINTENANCE
    assert s.urgency == Urgency.EMERGENCY
    assert s.tone == Tone.URGENT


def test_merge_returns_new_instance() -> None:
    s = AgentState(tenant_id="t-1", request_id="r-1")
    s2 = s.merge({"intent": Intent.MAINTENANCE})
    assert s2 is not s
    assert s.intent is None  # original unchanged (immutable copy)
    assert s2.intent == Intent.MAINTENANCE


def test_merge_rejects_unknown_keys() -> None:
    """Unknown keys in merge() = node bug; Pydantic should raise loudly."""
    s = AgentState(tenant_id="t-1", request_id="r-1")
    with pytest.raises(ValidationError):
        s.merge({"not_a_field": "oops"})


def test_serialization_roundtrip() -> None:
    s = AgentState(
        tenant_id="t-1",
        request_id="r-1",
        channel=Channel.WEB,
        cost_so_far=1.25,
        routes=["triage", "knowledge"],
    )
    blob = s.model_dump_json()
    s2 = AgentState.model_validate_json(blob)
    assert s2 == s


def test_all_ac_fields_present() -> None:
    """Regression — 13 CM-28 fields + ``escalation`` (CM-32) + ``sub_intents``
    (CM-87) + ``sub_results`` (CM-88) = 16 exact fields."""
    expected = {
        "tenant_id",
        "request_id",
        "channel",
        "raw_message",
        "normalized",
        "intent",
        "urgency",
        "tone",
        "sub_intents",  # CM-87
        "sub_results",  # CM-88
        "history",
        "cost_so_far",
        "search_count",
        "routes",
        "output",
        "escalation",  # CM-32
    }
    assert set(AgentState.model_fields.keys()) == expected
