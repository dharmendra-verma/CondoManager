"""Escalation Agent tests (CM-32) — classification, record assembly, node.

All offline (no OpenAI calls). The LLM path is exercised with a fake
structured-output runnable. The end-to-end HITL legal gate lives in
``test_hitl.py`` (it needs a full graph run for ``interrupt()``).
"""

from __future__ import annotations

import pytest
from agents.orchestrator import AgentState, nodes
from agents.orchestrator.escalation import (
    EscalationClassification,
    EscalationClassifier,
    HeuristicEscalationClassifier,
    LLMEscalationClassifier,
    build_empathetic_draft,
    build_record,
    compose_manager_alert,
    get_escalation_classifier,
    severity_for,
)
from agents.orchestrator.state import EscalationCategory, Tone, Urgency
from pydantic import ValidationError

# --- Schema ------------------------------------------------------------------


def test_classification_valid() -> None:
    c = EscalationClassification(category=EscalationCategory.LEGAL, legal_risk=True)
    assert c.category is EscalationCategory.LEGAL
    assert c.legal_risk is True


def test_classification_rejects_bad_category() -> None:
    with pytest.raises(ValidationError):
        EscalationClassification(category="nonsense")  # type: ignore[arg-type]


# --- Heuristic classifier ----------------------------------------------------


def test_heuristic_is_a_classifier() -> None:
    assert isinstance(HeuristicEscalationClassifier(), EscalationClassifier)


@pytest.mark.parametrize(
    ("message", "expected_legal"),
    [
        ("I'm calling my lawyer and suing you", True),
        ("The mold made me sick, my doctor says so", True),
        ("I will hold you liable for this", True),
        ("The plumber never showed up again", False),
        ("Nobody has responded to my emails", False),
    ],
)
def test_heuristic_legal_flag(message: str, expected_legal: bool) -> None:
    c = HeuristicEscalationClassifier().classify(message, [])
    assert c.legal_risk is expected_legal


def test_heuristic_categories() -> None:
    h = HeuristicEscalationClassifier()
    assert h.classify("I'll see you in court", []).category is EscalationCategory.LEGAL
    assert h.classify("there is a gas hazard", []).category is EscalationCategory.SAFETY
    assert (
        h.classify("this is the third time I report it", []).category
        is EscalationCategory.REPEAT
    )
    assert (
        h.classify("nobody got back to me", []).category
        is EscalationCategory.COMMUNICATION_BREAKDOWN
    )


def test_heuristic_costs_nothing() -> None:
    assert HeuristicEscalationClassifier().cost_per_call_usd == 0.0


# --- LLM classifier (mocked runnable) ----------------------------------------


class _FakeRunnable:
    def __init__(self, return_value: object) -> None:
        self.return_value = return_value

    def invoke(self, messages: object) -> object:
        return self.return_value


def _llm_with_fake(return_value: object) -> LLMEscalationClassifier:
    clf = LLMEscalationClassifier.__new__(LLMEscalationClassifier)
    clf._model = "gpt-4o-mini"  # type: ignore[attr-defined]
    clf._llm = _FakeRunnable(return_value)  # type: ignore[attr-defined]
    return clf


def test_llm_classify_returns_parsed() -> None:
    expected = EscalationClassification(
        category=EscalationCategory.LEGAL, legal_risk=True
    )
    assert _llm_with_fake(expected).classify("...", []) is expected


def test_llm_classify_rejects_non_model() -> None:
    with pytest.raises(AssertionError):
        _llm_with_fake({"category": "legal"}).classify("...", [])


# --- Selector ----------------------------------------------------------------


def test_selector_heuristic_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert isinstance(get_escalation_classifier(), HeuristicEscalationClassifier)


def test_selector_llm_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    assert isinstance(get_escalation_classifier(), LLMEscalationClassifier)


# --- Record assembly ---------------------------------------------------------


def test_severity_critical_for_legal_and_safety() -> None:
    assert severity_for(EscalationClassification(category=EscalationCategory.LEGAL)) == "critical"
    assert severity_for(EscalationClassification(category=EscalationCategory.SAFETY)) == "critical"
    assert severity_for(
        EscalationClassification(category=EscalationCategory.SERVICE_FAILURE, legal_risk=True)
    ) == "critical"
    assert severity_for(
        EscalationClassification(category=EscalationCategory.REPEAT)
    ) == "high"


def test_build_record_populates_fields() -> None:
    c = EscalationClassification(category=EscalationCategory.REPEAT, legal_risk=False)
    rec = build_record(
        record_id="esc-1",
        tenant_id="t-1",
        request_id="r-1",
        classification=c,
        urgency=Urgency.HIGH,
        tone=Tone.FRUSTRATED,
        message="this keeps happening",
    )
    assert rec.record_id == "esc-1"
    assert rec.category is EscalationCategory.REPEAT
    assert rec.status == "pending_review"
    assert rec.hitl_required is True
    assert rec.tenant_draft  # non-empty held draft
    assert rec.manager_alert


def test_manager_alert_flags_legal() -> None:
    c = EscalationClassification(category=EscalationCategory.LEGAL, legal_risk=True)
    alert = compose_manager_alert(
        tenant_id="t-1", classification=c, urgency=Urgency.HIGH, tone=Tone.ANGRY,
        message="lawyer incoming",
    )
    assert "LEGAL RISK FLAGGED" in alert
    assert "t-1" in alert


def test_empathetic_draft_is_non_committal() -> None:
    c = EscalationClassification(category=EscalationCategory.SERVICE_FAILURE)
    draft = build_empathetic_draft("the repair failed", c, Tone.ANGRY)
    assert draft
    # Held drafts must not commit to a specific remedy/timeline before review.
    lowered = draft.lower()
    assert "sorry" in lowered or "thank you" in lowered


# --- Node integration --------------------------------------------------------


class _FakeClassifier:
    cost_per_call_usd = 0.5

    def __init__(self, result: EscalationClassification) -> None:
        self.result = result
        self.seen: str | None = None

    def classify(self, message: str, history: list[dict]) -> EscalationClassification:
        self.seen = message
        return self.result


class _SpyStore:
    def __init__(self) -> None:
        self.saved: list = []

    def save(self, record) -> None:  # noqa: ANN001
        self.saved.append(record)

    def recent(self, tenant_id: str, *, limit: int = 10) -> list:
        return list(self.saved)


class _SpyNotifier:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.notified: list = []

    def notify(self, record) -> bool:  # noqa: ANN001
        self.notified.append(record)
        return self.ok


def _wire(monkeypatch: pytest.MonkeyPatch, classification: EscalationClassification):
    clf = _FakeClassifier(classification)
    store = _SpyStore()
    notifier = _SpyNotifier()
    monkeypatch.setattr(nodes, "get_escalation_classifier", lambda: clf)
    monkeypatch.setattr(nodes, "get_escalation_store", lambda: store)
    monkeypatch.setattr(nodes, "get_manager_notifier", lambda: notifier)
    return clf, store, notifier


def test_escalation_node_records_alerts_and_holds_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clf, store, notifier = _wire(
        monkeypatch,
        EscalationClassification(category=EscalationCategory.LEGAL, legal_risk=True),
    )
    state = AgentState(
        tenant_id="t-9", request_id="r-9", raw_message="I'm suing", urgency=Urgency.HIGH,
        tone=Tone.ANGRY, cost_so_far=1.0,
    )
    out = nodes.escalation(state)

    assert out["routes"] == ["hitl_review"]
    assert out["escalation"].legal_risk is True
    assert out["escalation"].status == "pending_review"
    # Draft is HELD in output, never sent here.
    assert out["output"]["draft"]
    assert out["output"]["status"] == "escalation_pending_review"
    assert out["output"]["legal_risk"] is True
    assert "sent" not in out["output"]  # the escalation node never sends
    assert out["cost_so_far"] == pytest.approx(1.5)
    # Record persisted + manager alerted.
    assert len(store.saved) == 1
    assert len(notifier.notified) == 1


def test_escalation_node_guardrail_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, store, notifier = _wire(
        monkeypatch, EscalationClassification(category=EscalationCategory.REPEAT)
    )
    state = AgentState(
        tenant_id="t-1", request_id="r-1", raw_message="x", cost_so_far=999.0
    )
    out = nodes.escalation(state)
    assert out["output"]["status"] == "guardrail_terminated"
    # Classifier/store/notifier never invoked.
    assert store.saved == []
    assert notifier.notified == []
