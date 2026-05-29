"""End-to-end MaintenanceAgent tests (CM-31)."""

from __future__ import annotations

from datetime import UTC, datetime

from agents.maintenance.agent import MaintenanceAgent
from agents.maintenance.repository import InMemoryTicketRepository
from agents.maintenance.schema import Priority, TicketStatus
from agents.orchestrator.state import AgentState, Tone, Urgency

_NOW = datetime(2026, 5, 29, 12, 0, 0, tzinfo=UTC)


class _CapturingNotifier:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def notify_manager(self, notification: dict[str, str]) -> None:
        self.calls.append(notification)


def _agent(repo: InMemoryTicketRepository, notifier: _CapturingNotifier) -> MaintenanceAgent:
    codes = iter(["TKT-CODE0001", "TKT-CODE0002", "TKT-CODE0003"])
    return MaintenanceAgent(
        repository=repo,
        notifier=notifier,
        code_generator=lambda: next(codes),
        now_fn=lambda: _NOW,
    )


def _state(message: str, **kw: object) -> AgentState:
    return AgentState(tenant_id="t-1", request_id="r-1", raw_message=message, **kw)


def test_new_ticket_created_and_manager_notified() -> None:
    repo = InMemoryTicketRepository()
    notifier = _CapturingNotifier()
    out = _agent(repo, notifier).handle(_state("Leak under the sink in unit 4B"))["output"]

    assert out["status"] == "ticket_created"
    assert out["ticket_id"] == "TKT-CODE0001"
    assert out["unit"] == "4b"
    assert out["category"] == "plumbing"
    assert "TKT-CODE0001" in out["confirmation"]
    # Manager notified exactly once for a new ticket (AC5).
    assert len(notifier.calls) == 1
    assert notifier.calls[0]["ticket_id"] == "TKT-CODE0001"
    # Persisted.
    assert len(repo.recent_for_unit("t-1", "4b", since=datetime(2026, 5, 22, tzinfo=UTC))) == 1


def test_duplicate_links_to_original_and_skips_notify() -> None:
    repo = InMemoryTicketRepository()
    notifier = _CapturingNotifier()
    agent = _agent(repo, notifier)

    first = agent.handle(_state("Leak under the sink in unit 4B"))["output"]
    second = agent.handle(_state("the sink in unit 4B is leaking again"))["output"]

    assert first["status"] == "ticket_created"
    assert second["status"] == "duplicate"
    assert second["duplicate_of"] == first["ticket_id"]
    # No second ticket persisted, no second manager page.
    assert len(notifier.calls) == 1


def test_emergency_tone_drives_priority() -> None:
    repo = InMemoryTicketRepository()
    notifier = _CapturingNotifier()
    state = _state(
        "Water is pouring from the ceiling in unit 4B",
        urgency=Urgency.EMERGENCY,
        tone=Tone.URGENT,
    )
    out = _agent(repo, notifier).handle(state)["output"]
    assert out["priority"] == str(Priority.P1)
    assert out["eta"] == "within 2 hours"


def test_unknown_unit_still_creates_ticket_without_dedup() -> None:
    repo = InMemoryTicketRepository()
    notifier = _CapturingNotifier()
    agent = _agent(repo, notifier)
    # Two unknown-unit reports of the same issue must NOT merge.
    first = agent.handle(_state("the lobby sink is leaking"))["output"]
    second = agent.handle(_state("the lobby sink is leaking"))["output"]
    assert first["status"] == "ticket_created"
    assert second["status"] == "ticket_created"
    assert first["unit"] == "unknown"


def test_repeat_after_resolved_bumps_priority() -> None:
    repo = InMemoryTicketRepository()
    notifier = _CapturingNotifier()
    agent = _agent(repo, notifier)

    first = agent.handle(_state("sink leaking in unit 4B", urgency=Urgency.LOW))["output"]
    # Resolve it, then a recurrence arrives.
    stored = repo.recent_for_unit("t-1", "4b", since=datetime(2026, 5, 22, tzinfo=UTC))[0]
    stored.status = TicketStatus.RESOLVED
    second = agent.handle(_state("sink leaking again in unit 4B", urgency=Urgency.LOW))["output"]

    assert first["priority"] == str(Priority.P4)
    # Repeat (resolved prior, same unit+category) bumps LOW -> P3.
    assert second["status"] == "ticket_created"
    assert second["priority"] == str(Priority.P3)
    assert second["is_repeat"] is True
