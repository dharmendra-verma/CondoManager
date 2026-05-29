"""Escalation-store seam tests (CM-32 AC #3)."""

from __future__ import annotations

import pytest
from agents.orchestrator.escalation_store import (
    EscalationStore,
    NoopEscalationStore,
    get_escalation_store,
)
from agents.orchestrator.state import EscalationCategory, EscalationRecord


def _rec(tenant: str, rid: str) -> EscalationRecord:
    return EscalationRecord(
        record_id=rid,
        tenant_id=tenant,
        request_id="r-1",
        category=EscalationCategory.REPEAT,
    )


def test_noop_is_a_store() -> None:
    assert isinstance(NoopEscalationStore(), EscalationStore)


def test_noop_save_and_recent_roundtrip() -> None:
    store = NoopEscalationStore()
    store.save(_rec("t-1", "esc-1"))
    store.save(_rec("t-1", "esc-2"))
    store.save(_rec("t-2", "esc-3"))
    recent = store.recent("t-1")
    assert [r.record_id for r in recent] == ["esc-2", "esc-1"]  # newest first
    assert store.recent("t-2")[0].record_id == "esc-3"
    assert store.recent("unknown") == []


def test_selector_returns_noop_without_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    assert isinstance(get_escalation_store(), NoopEscalationStore)


def test_selector_treats_placeholder_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSMOS_ENDPOINT", "REPLACE-ME")
    assert isinstance(get_escalation_store(), NoopEscalationStore)
