"""Triage Agent tests (CM-30).

Covers the classification schema, the routing matrix, both classifier
implementations, the env-driven selector, and the ``triage`` node body —
all offline (no OpenAI calls). The ``LLMTriageClassifier.classify`` path is
exercised with a fake structured-output runnable so the contract is pinned
by a test rather than a live model call.
"""

from __future__ import annotations

import datetime as dt

import pytest
from agents.channels.schema import Channel, NormalizedMessage
from agents.orchestrator import AgentState, nodes
from agents.orchestrator.state import Intent, Tone, Urgency
from agents.orchestrator.triage import (
    HeuristicTriageClassifier,
    LLMTriageClassifier,
    TriageClassification,
    TriageClassifier,
    format_history,
    get_triage_classifier,
    route_for,
)
from pydantic import ValidationError

# --- Schema ------------------------------------------------------------------


def test_classification_accepts_valid_enum_values() -> None:
    c = TriageClassification(
        intent=Intent.MAINTENANCE, urgency=Urgency.HIGH, tone=Tone.FRUSTRATED
    )
    assert c.intent is Intent.MAINTENANCE
    assert c.confidence == 1.0  # default
    assert c.rationale == ""


def test_classification_rejects_out_of_enum_value() -> None:
    with pytest.raises(ValidationError):
        TriageClassification(intent="complaint", urgency="high", tone="neutral")  # type: ignore[arg-type]


def test_classification_confidence_is_bounded() -> None:
    with pytest.raises(ValidationError):
        TriageClassification(
            intent=Intent.INQUIRY, urgency=Urgency.LOW, tone=Tone.NEUTRAL, confidence=1.5
        )


# --- Routing matrix (AC #6) --------------------------------------------------


@pytest.mark.parametrize(
    ("intent", "expected_route"),
    [
        (Intent.MAINTENANCE, "maintenance"),
        (Intent.INQUIRY, "knowledge"),
        (Intent.ESCALATION, "escalation"),
        (Intent.FOLLOW_UP, "maintenance"),
        (Intent.UNKNOWN, "knowledge"),
    ],
)
def test_route_for_maps_every_intent(intent: Intent, expected_route: str) -> None:
    c = TriageClassification(intent=intent, urgency=Urgency.LOW, tone=Tone.NEUTRAL)
    assert route_for(c) == expected_route


def test_routes_are_valid_graph_targets() -> None:
    """Every route the matrix can return must be a real downstream node."""
    valid = {"knowledge", "maintenance", "escalation"}
    for intent in Intent:
        c = TriageClassification(intent=intent, urgency=Urgency.LOW, tone=Tone.NEUTRAL)
        assert route_for(c) in valid


# --- Heuristic classifier (CM-28 keyword parity) -----------------------------


def test_heuristic_is_a_triage_classifier() -> None:
    assert isinstance(HeuristicTriageClassifier(), TriageClassifier)


@pytest.mark.parametrize(
    ("message", "expected_intent"),
    [
        ("Please escalate, I want a human", Intent.ESCALATION),
        ("The kitchen sink has a leak", Intent.MAINTENANCE),
        ("Something is broken in here", Intent.MAINTENANCE),
        ("any update on my ticket #12", Intent.FOLLOW_UP),
        ("when does the gym open", Intent.INQUIRY),
    ],
)
def test_heuristic_keyword_routing(message: str, expected_intent: Intent) -> None:
    c = HeuristicTriageClassifier().classify(message, [])
    assert c.intent is expected_intent


def test_heuristic_preserves_cm28_stub_routes() -> None:
    """The three CM-28 stub keywords must still land on the same routes so
    tests/orchestrator/test_hello_world.py stays green."""
    h = HeuristicTriageClassifier()
    assert route_for(h.classify("a human please", [])) == "escalation"
    assert route_for(h.classify("the pipe has a leak", [])) == "maintenance"
    assert route_for(h.classify("ping", [])) == "knowledge"


def test_heuristic_flags_emergency_and_shouting() -> None:
    c = HeuristicTriageClassifier().classify("THE BASEMENT IS FLOODING", [])
    assert c.urgency is Urgency.EMERGENCY
    # ALL-CAPS shouting reads as angry tone.
    assert c.tone is Tone.ANGRY


def test_heuristic_costs_nothing() -> None:
    assert HeuristicTriageClassifier().cost_per_call_usd == 0.0


# --- LLM classifier (mocked structured-output runnable) ----------------------


class _FakeRunnable:
    """Stands in for ``ChatOpenAI(...).with_structured_output(...)``."""

    def __init__(self, return_value: object) -> None:
        self.return_value = return_value
        self.last_messages: object = None

    def invoke(self, messages: object) -> object:
        self.last_messages = messages
        return self.return_value


def _llm_with_fake(return_value: object) -> LLMTriageClassifier:
    """Build an LLMTriageClassifier without touching OpenAI."""
    clf = LLMTriageClassifier.__new__(LLMTriageClassifier)
    clf._model = "gpt-4o-mini"  # type: ignore[attr-defined]
    clf._llm = _FakeRunnable(return_value)  # type: ignore[attr-defined]
    return clf


def test_llm_classify_returns_parsed_classification() -> None:
    expected = TriageClassification(
        intent=Intent.ESCALATION, urgency=Urgency.HIGH, tone=Tone.ANGRY
    )
    clf = _llm_with_fake(expected)
    result = clf.classify("I demand a manager now", [])
    assert result is expected


def test_llm_classify_rejects_non_model_response() -> None:
    """A structured-output contract break surfaces loudly, not silently."""
    clf = _llm_with_fake({"intent": "escalation"})  # wrong type
    with pytest.raises(AssertionError):
        clf.classify("hello", [])


def test_llm_cost_estimate_is_positive() -> None:
    assert LLMTriageClassifier.cost_per_call_usd > 0.0


# --- Selector ----------------------------------------------------------------


def test_selector_returns_heuristic_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert isinstance(get_triage_classifier(), HeuristicTriageClassifier)


def test_selector_treats_placeholder_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "REPLACE-ME")
    assert isinstance(get_triage_classifier(), HeuristicTriageClassifier)


def test_selector_returns_llm_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # A dummy key lets ChatOpenAI construct (no network at construction time).
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    assert isinstance(get_triage_classifier(), LLMTriageClassifier)


# --- format_history ----------------------------------------------------------


def test_format_history_empty() -> None:
    assert "no prior tickets" in format_history([])


def test_format_history_renders_tickets() -> None:
    rendered = format_history(
        [{"ticket_id": "4521", "status": "open", "summary": "leaking faucet"}]
    )
    assert "#4521" in rendered
    assert "open" in rendered
    assert "leaking faucet" in rendered


# --- Node integration --------------------------------------------------------


class _FakeClassifier:
    """Records the message it was handed; returns a fixed classification."""

    cost_per_call_usd = 0.25

    def __init__(self, result: TriageClassification) -> None:
        self.result = result
        self.seen_message: str | None = None
        self.seen_history: list[dict] | None = None

    def classify(self, message: str, history: list[dict]) -> TriageClassification:
        self.seen_message = message
        self.seen_history = history
        return self.result


class _ExplodingClassifier:
    cost_per_call_usd = 0.0

    def classify(self, message: str, history: list[dict]) -> TriageClassification:
        raise AssertionError("classifier must not be called")


def _state(**kw: object) -> AgentState:
    base: dict[str, object] = {"tenant_id": "t-1", "request_id": "r-1"}
    base.update(kw)
    return AgentState(**base)  # type: ignore[arg-type]


def test_node_sets_fields_and_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClassifier(
        TriageClassification(
            intent=Intent.MAINTENANCE, urgency=Urgency.HIGH, tone=Tone.FRUSTRATED
        )
    )
    monkeypatch.setattr(nodes, "get_triage_classifier", lambda: fake)
    monkeypatch.setattr(
        nodes, "get_history_provider", lambda: _HistoryStub([{"ticket_id": "9"}])
    )

    out = nodes.triage(_state(raw_message="no hot water", cost_so_far=1.0))

    assert out["intent"] is Intent.MAINTENANCE
    assert out["urgency"] is Urgency.HIGH
    assert out["tone"] is Tone.FRUSTRATED
    assert out["routes"] == ["maintenance"]
    assert out["history"] == [{"ticket_id": "9"}]
    # Cost bumped by the classifier's per-call estimate.
    assert out["cost_so_far"] == pytest.approx(1.25)
    # History was passed to the classifier.
    assert fake.seen_history == [{"ticket_id": "9"}]


def test_node_prefers_normalized_content(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClassifier(
        TriageClassification(
            intent=Intent.INQUIRY, urgency=Urgency.LOW, tone=Tone.NEUTRAL
        )
    )
    monkeypatch.setattr(nodes, "get_triage_classifier", lambda: fake)
    monkeypatch.setattr(nodes, "get_history_provider", lambda: _HistoryStub([]))

    now = dt.datetime.now(dt.UTC)
    normalized = NormalizedMessage(
        channel=Channel.WEB,
        tenant_id="t-1",
        sender_id="s-1",
        content="masked web content",
        received_at=now,
        received_by_us_at=now,
        upstream_message_id="u-1",
    )
    nodes.triage(_state(raw_message="raw fallback", normalized=normalized))
    assert fake.seen_message == "masked web content"


def test_node_short_circuits_on_guardrail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nodes, "get_triage_classifier", lambda: _ExplodingClassifier())
    monkeypatch.setattr(nodes, "get_history_provider", lambda: _HistoryStub([]))

    out = nodes.triage(_state(raw_message="ping", cost_so_far=999.0))
    assert out["output"]["status"] == "guardrail_terminated"
    assert out["routes"] == ["guardrail_terminated"]


class _HistoryStub:
    def __init__(self, tickets: list[dict]) -> None:
        self._tickets = tickets

    def recent_tickets(self, tenant_id: str, *, limit: int = 5) -> list[dict]:
        return self._tickets
