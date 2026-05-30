"""Audit log tests (CM-38 AC4)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from agents.security.audit import (
    InMemoryAuditSink,
    get_audit_sink,
    record_audit,
)
from agents.security.models import AuditAction, AuditEvent


def test_record_appends(now: datetime) -> None:
    sink = InMemoryAuditSink()
    record_audit(
        AuditAction.READ,
        actor="manager@cm",
        resource="tickets/TKT-1",
        tenant_id="t-1",
        sink=sink,
        now=now,
        event_id="e1",
    )
    record_audit(
        AuditAction.UPDATE,
        actor="agent",
        resource="tickets/TKT-1",
        tenant_id="t-1",
        sink=sink,
        now=now,
        event_id="e2",
    )
    assert [e.id for e in sink.events] == ["e1", "e2"]
    assert sink.events[0].action is AuditAction.READ


def test_detail_is_masked(now: datetime) -> None:
    sink = InMemoryAuditSink()
    event = record_audit(
        AuditAction.ACCESS,
        actor="agent",
        resource="conversations/c-1",
        detail="tenant jane@example.com asked about rent",
        sink=sink,
        now=now,
        event_id="e1",
    )
    assert "jane@example.com" not in event.detail
    assert "***@***.***" in event.detail


def test_audit_event_is_immutable() -> None:
    event = AuditEvent(
        id="e1",
        ts=datetime(2026, 5, 30, tzinfo=UTC),
        action=AuditAction.CREATE,
        actor="agent",
        resource="tickets/TKT-1",
    )
    with pytest.raises(Exception):  # noqa: B017 — pydantic frozen raises ValidationError
        event.actor = "someone-else"  # type: ignore[misc]


def test_events_view_is_immutable_snapshot() -> None:
    sink = InMemoryAuditSink()
    record_audit(AuditAction.READ, actor="a", resource="r", sink=sink, event_id="e1")
    events = sink.events
    assert isinstance(events, tuple)  # can't append to the returned view


def test_to_doc_partitions_system_actions_without_tenant() -> None:
    event = AuditEvent(
        id="e1",
        ts=datetime(2026, 5, 30, tzinfo=UTC),
        action=AuditAction.ERASE,
        actor="system",
        resource="tenant/t-9",
        tenant_id=None,
    )
    doc = event.to_doc()
    assert doc["tenantId"] == "_system"


def test_to_doc_uses_tenant_partition_when_present() -> None:
    event = AuditEvent(
        id="e1",
        ts=datetime(2026, 5, 30, tzinfo=UTC),
        action=AuditAction.READ,
        actor="manager",
        resource="tickets/TKT-1",
        tenant_id="t-1",
    )
    assert event.to_doc()["tenantId"] == "t-1"


def test_get_audit_sink_offline_default_is_in_memory() -> None:
    # conftest clears COSMOS_ENDPOINT.
    assert isinstance(get_audit_sink(), InMemoryAuditSink)


def test_get_audit_sink_is_cached() -> None:
    assert get_audit_sink() is get_audit_sink()
