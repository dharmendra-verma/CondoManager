"""Tenant-reply dispatch over the originating channel (CM-50)."""

from __future__ import annotations

from datetime import UTC, datetime

from agents.channels.schema import Channel, NormalizedMessage
from agents.maintenance.agent import MaintenanceAgent
from agents.maintenance.repository import InMemoryTicketRepository
from agents.orchestrator.state import AgentState

_NOW = datetime(2026, 5, 29, 12, 0, 0, tzinfo=UTC)


class _CapturingOutbound:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(
        self, recipient: str, message: str, attachments: list[str] | None = None
    ) -> bool:
        self.sent.append((recipient, message))
        return True


class _NoopNotifier:
    def notify_manager(self, notification: dict[str, str]) -> None:
        return None


def _normalized(channel: Channel, sender_id: str, content: str) -> NormalizedMessage:
    return NormalizedMessage(
        channel=channel,
        tenant_id="t-1",
        sender_id=sender_id,
        content=content,
        received_at=_NOW,
        received_by_us_at=_NOW,
        upstream_message_id="msg-1",
    )


def _agent(
    outbound: _CapturingOutbound,
    *,
    repo: InMemoryTicketRepository | None = None,
    codes: object = None,
) -> MaintenanceAgent:
    gen = codes if codes is not None else (lambda: "TKT-CODE0001")
    return MaintenanceAgent(
        repository=repo or InMemoryTicketRepository(),
        notifier=_NoopNotifier(),
        outbound=outbound,
        code_generator=gen,  # type: ignore[arg-type]
        now_fn=lambda: _NOW,
    )


def test_new_ticket_sends_confirmation_over_originating_channel() -> None:
    outbound = _CapturingOutbound()
    state = AgentState(
        tenant_id="t-1",
        request_id="r-1",
        normalized=_normalized(
            Channel.WHATSAPP, "+15551234567", "Leak under the sink in unit 4B"
        ),
    )
    out = _agent(outbound).handle(state)["output"]
    assert out["status"] == "ticket_created"
    assert len(outbound.sent) == 1
    recipient, message = outbound.sent[0]
    assert recipient == "+15551234567"
    assert message == out["confirmation"]


def test_offline_state_does_not_send() -> None:
    outbound = _CapturingOutbound()
    state = AgentState(
        tenant_id="t-1", request_id="r-1", raw_message="Leak under the sink in unit 4B"
    )
    out = _agent(outbound).handle(state)["output"]
    assert out["status"] == "ticket_created"
    assert outbound.sent == []  # no normalized -> nothing to push


def test_duplicate_reply_also_sent() -> None:
    outbound = _CapturingOutbound()
    repo = InMemoryTicketRepository()
    codes = iter(["TKT-CODE0001", "TKT-CODE0002"])
    agent = _agent(outbound, repo=repo, codes=lambda: next(codes))

    agent.handle(
        AgentState(
            tenant_id="t-1",
            request_id="r-1",
            normalized=_normalized(
                Channel.WHATSAPP, "+15551234567", "Leak under the sink in unit 4B"
            ),
        )
    )
    second = agent.handle(
        AgentState(
            tenant_id="t-1",
            request_id="r-1",
            normalized=_normalized(
                Channel.WHATSAPP, "+15551234567", "the sink in unit 4B is leaking again"
            ),
        )
    )["output"]

    assert second["status"] == "duplicate"
    # Both the create and the duplicate confirmation were delivered.
    assert len(outbound.sent) == 2
