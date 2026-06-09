"""Tests for the specialist-agent tools (CM-86, Track B).

Verifies, per tool: a valid name/description/args-schema; that the adapter maps
the tool args onto the right ``AgentState`` fields; that the tool's result
equals calling the underlying agent directly with the same state (the core
"thin adapter, no behaviour change" guarantee); that every tool runs offline
and round-trips through ``json.dumps``; and the documented edge cases.

Determinism: the agents use ``uuid``/``now``, so the equality tests monkeypatch
the module-level builders to inject fixed code-generator / clock / record-id and
fresh in-memory repositories — two independent runs then produce identical dicts.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from agents.coordinator import (
    ALL_TOOLS,
    EscalationToolArgs,
    KnowledgeToolArgs,
    MaintenanceToolArgs,
    VendorToolArgs,
    escalation_tool,
    knowledge_tool,
    maintenance_tool,
    vendor_tool,
)
from agents.coordinator import tools as tools_mod
from agents.eval.lexical import LexicalRetriever, kb_chunk
from agents.knowledge.planner import StubKnowledgePlanner
from agents.maintenance import MaintenanceAgent
from agents.maintenance.repository import InMemoryTicketRepository
from agents.orchestrator.escalation import HeuristicEscalationClassifier, build_record
from agents.orchestrator.state import AgentState, Intent, Tone, Urgency
from agents.vendor import VendorAgent
from pydantic import BaseModel

_FIXED_NOW = datetime(2026, 5, 29, 12, 0, 0, tzinfo=UTC)


# --- Structure / schema -------------------------------------------------------


def test_all_tools_registered() -> None:
    assert len(ALL_TOOLS) == 4
    names = [t.name for t in ALL_TOOLS]
    assert names == [
        "maintenance_agent",
        "vendor_agent",
        "knowledge_agent",
        "escalation_agent",
    ]
    assert len(set(names)) == 4  # unique
    for tool in ALL_TOOLS:
        assert tool.description.strip()  # non-empty description for LLM selection
        assert isinstance(tool.args_schema, type)
        assert issubclass(tool.args_schema, BaseModel)


@pytest.mark.parametrize(
    ("tool", "schema", "required_fields"),
    [
        (maintenance_tool, MaintenanceToolArgs, {"tenant_id", "message"}),
        (
            vendor_tool,
            VendorToolArgs,
            {"tenant_id", "category", "priority", "ticket_id", "unit"},
        ),
        (knowledge_tool, KnowledgeToolArgs, {"tenant_id", "question"}),
        (escalation_tool, EscalationToolArgs, {"tenant_id", "message"}),
    ],
)
def test_tool_schema_fields(
    tool: Any, schema: type[BaseModel], required_fields: set[str]
) -> None:
    assert tool.args_schema is schema
    assert required_fields <= set(schema.model_fields)


# --- Arg adaptation (args -> AgentState) --------------------------------------


class _SpyAgent:
    """Captures the ``AgentState`` it is handed; returns a sentinel."""

    def __init__(self) -> None:
        self.seen: AgentState | None = None

    def handle(self, state: AgentState) -> dict[str, Any]:
        self.seen = state
        return {"output": {"sentinel": True}}


def test_maintenance_arg_adaptation(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _SpyAgent()
    monkeypatch.setattr(tools_mod, "_maintenance_agent", lambda: spy)

    maintenance_tool.invoke(
        {
            "tenant_id": "t-7",
            "message": "Heater is dead in unit 12C",
            "urgency": "high",
            "tone": "frustrated",
            "request_id": "req-1",
        }
    )

    state = spy.seen
    assert state is not None
    assert state.tenant_id == "t-7"
    assert state.request_id == "req-1"
    assert state.raw_message == "Heater is dead in unit 12C"
    assert state.urgency is Urgency.HIGH
    assert state.tone is Tone.FRUSTRATED


def test_vendor_arg_adaptation(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _SpyAgent()
    monkeypatch.setattr(tools_mod, "_vendor_agent", lambda: spy)

    vendor_tool.invoke(
        {
            "tenant_id": "t-7",
            "category": "plumbing",
            "priority": "P2",
            "ticket_id": "TKT-1",
            "unit": "4B",
            "intent": "maintenance",
        }
    )

    state = spy.seen
    assert state is not None
    assert state.intent is Intent.MAINTENANCE
    assert state.output == {
        "status": "ticket_created",
        "category": "plumbing",
        "priority": "P2",
        "ticket_id": "TKT-1",
        "unit": "4B",
    }


# --- Tool == direct agent call ------------------------------------------------


def _fixed_maintenance_agent() -> MaintenanceAgent:
    """Fresh agent with fixed code/clock + fresh repo — deterministic output."""
    return MaintenanceAgent(
        repository=InMemoryTicketRepository(),
        code_generator=lambda: "TKT-FIXED01",
        now_fn=lambda: _FIXED_NOW,
    )


def test_maintenance_tool_equals_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools_mod, "_maintenance_agent", _fixed_maintenance_agent)

    args = {
        "tenant_id": "t-1",
        "message": "Water leaking under the sink in unit 4B",
        "urgency": "high",
    }
    tool_result = maintenance_tool.invoke(args)

    direct = _fixed_maintenance_agent().handle(
        AgentState(
            tenant_id="t-1",
            request_id="",
            raw_message="Water leaking under the sink in unit 4B",
            urgency=Urgency.HIGH,
        )
    )

    assert tool_result == direct
    assert tool_result["output"]["status"] == "ticket_created"
    assert tool_result["output"]["ticket_id"] == "TKT-FIXED01"


def _fixed_vendor_agent() -> VendorAgent:
    """Fresh agent with a fixed clock — deterministic matching + timestamps."""
    return VendorAgent(now_fn=lambda: _FIXED_NOW)


def test_vendor_tool_equals_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools_mod, "_vendor_agent", _fixed_vendor_agent)

    args = {
        "tenant_id": "t-1",
        "category": "plumbing",
        "priority": "P2",
        "ticket_id": "TKT-1",
        "unit": "4B",
        "intent": "maintenance",
    }
    tool_result = vendor_tool.invoke(args)

    direct = _fixed_vendor_agent().handle(
        AgentState(
            tenant_id="t-1",
            request_id="",
            intent=Intent.MAINTENANCE,
            output={
                "status": "ticket_created",
                "category": "plumbing",
                "priority": "P2",
                "ticket_id": "TKT-1",
                "unit": "4B",
            },
        )
    )

    assert tool_result == direct
    assert tool_result["routes"]  # a real ticket produced a routing decision


def test_vendor_passthrough_non_ticket() -> None:
    """A non-'ticket_created' upstream output passes straight through to END."""
    result = vendor_tool.invoke(
        {
            "tenant_id": "t-1",
            "category": "plumbing",
            "priority": "P2",
            "ticket_id": "TKT-1",
            "unit": "4B",
            "status": "duplicate",
        }
    )
    assert result == {"routes": ["vendor_done"]}


def test_maintenance_duplicate_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same unit+issue twice on one injected repo -> 'duplicate' via the tool."""
    agent = _fixed_maintenance_agent()  # one shared repo across both calls
    monkeypatch.setattr(tools_mod, "_maintenance_agent", lambda: agent)

    args = {"tenant_id": "t-1", "message": "Leak under the sink in unit 4B"}
    first = maintenance_tool.invoke(args)
    second = maintenance_tool.invoke(args)

    assert first["output"]["status"] == "ticket_created"
    assert second["output"]["status"] == "duplicate"
    assert second["output"]["duplicate_of"] == first["output"]["ticket_id"]


# --- Knowledge tool -----------------------------------------------------------


def _quiet_hours_kb() -> LexicalRetriever:
    return LexicalRetriever(
        [
            kb_chunk(
                "house-rules",
                "Quiet hours policy: quiet hours are from 10pm to 7am daily.",
            ),
            kb_chunk("parking", "Parking permits are issued per unit on request."),
        ]
    )


def test_knowledge_tool_equals_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    retriever = _quiet_hours_kb()
    monkeypatch.setattr(tools_mod, "_knowledge_planner", StubKnowledgePlanner)
    monkeypatch.setattr(tools_mod, "_knowledge_store", lambda: retriever)
    monkeypatch.setattr(tools_mod, "_knowledge_embedder", lambda: retriever)

    question = "What are the quiet hours policy times?"
    tool_result = knowledge_tool.invoke({"tenant_id": "t-1", "question": question})

    direct = StubKnowledgePlanner().run(
        question,
        state=AgentState(tenant_id="t-1", request_id="", raw_message=question),
        store=retriever,
        embedder=retriever,
    )
    expected = tools_mod._knowledge_result_to_dict(direct)

    assert tool_result == expected
    assert tool_result["status"] == "answered"
    assert tool_result["refused"] is False
    assert tool_result["confidence"] >= 0.40


def test_knowledge_tool_offline_refuses() -> None:
    """With no store/embedder (default builders -> None) the tool refuses."""
    result = knowledge_tool.invoke(
        {"tenant_id": "t-1", "question": "What is the pet policy?"}
    )
    assert result["status"] == "refused"
    assert result["refused"] is True
    assert result["citations"] == []


# --- Escalation tool ----------------------------------------------------------


def test_escalation_tool_equals_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools_mod, "_new_record_id", lambda: "esc-FIXED")
    # CM-92: pin the classifier (as the record-id is pinned) so the tool path and
    # the direct baseline use the *same* deterministic instance. Otherwise, with a
    # real OPENAI_API_KEY both default to the LLM classifier and the two
    # independent classify() calls return different rationale text -> flaky.
    classifier = HeuristicEscalationClassifier()
    monkeypatch.setattr(tools_mod, "_escalation_classifier", lambda: classifier)

    message = "The mold made me sick and my doctor will be holding you liable."
    tool_result = escalation_tool.invoke({"tenant_id": "t-1", "message": message})

    classification = classifier.classify(message, [])
    direct = build_record(
        record_id="esc-FIXED",
        tenant_id="t-1",
        request_id="",
        classification=classification,
        urgency=None,
        tone=None,
        message=message,
    ).model_dump()

    assert tool_result == direct
    # legal/health cues -> legal_risk raised, HITL mandatory.
    assert tool_result["legal_risk"] is True
    assert tool_result["hitl_required"] is True
    assert tool_result["record_id"] == "esc-FIXED"


# --- Offline + JSON-serializable ----------------------------------------------


def test_all_tools_offline_json_serializable() -> None:
    """Every tool runs with no credentials and returns a JSON-serializable dict."""
    results = [
        maintenance_tool.invoke(
            {"tenant_id": "t-1", "message": "Leak in unit 4B", "urgency": "low"}
        ),
        vendor_tool.invoke(
            {
                "tenant_id": "t-1",
                "category": "plumbing",
                "priority": "P3",
                "ticket_id": "TKT-1",
                "unit": "4B",
            }
        ),
        knowledge_tool.invoke({"tenant_id": "t-1", "question": "Pet policy?"}),
        escalation_tool.invoke({"tenant_id": "t-1", "message": "I am being ignored."}),
    ]
    for result in results:
        assert isinstance(result, dict)
        assert json.loads(json.dumps(result)) == result
