"""Recent-ticket history lookup for Triage (CM-30 AC #5).

Jira: CM-30  | Epic: CM-5 (Agent 1 — Enhanced Triage Agent)  | Phase 1

Triage needs the tenant's recent tickets to recognise follow-ups ("any
update on #4521?") and to weight urgency (a repeated, still-open issue is
hotter than a first report). The durable tickets store doesn't exist yet —
it's CM-31 (Maintenance Agent) that creates the Cosmos ``tickets`` container
and writes to it. So this module ships the *seam*, not the store: a
``Protocol`` plus a no-op stub plus an env-driven selector, exactly the
pattern CM-29 used for its ``AudioTranscriber`` / ``ImageOcr`` preprocessor
stubs.

Today :class:`NoopTicketHistory` returns ``[]`` so Triage runs end-to-end
with no ticket store wired. CM-31 will register a Cosmos-backed provider
via :func:`get_history_provider` with **zero** changes to the Triage node.

A "ticket" dict is intentionally loose (``dict[str, Any]``) at this seam —
the durable schema is CM-31's to define. Triage only reads ``ticket_id`` /
``status`` / ``summary`` when rendering the prompt (see
``agents.orchestrator.triage.format_history``), tolerating their absence.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TicketHistoryProvider(Protocol):
    """Looks up a tenant's recent tickets for Triage context."""

    def recent_tickets(self, tenant_id: str, *, limit: int = 5) -> list[dict[str, Any]]:
        """Return up to ``limit`` most-recent tickets for ``tenant_id``.

        Newest first. Returns ``[]`` when the tenant has no tickets (or no
        store is wired). Implementations must not raise on an unknown tenant —
        an empty list is the correct answer.
        """
        ...


class NoopTicketHistory:
    """Default provider until the CM-31 Cosmos-backed tickets store lands.

    Always returns ``[]``. Triage degrades gracefully: with no history the
    prompt renders a "no prior tickets" line and the model classifies on the
    message alone.
    """

    def recent_tickets(self, tenant_id: str, *, limit: int = 5) -> list[dict[str, Any]]:
        return []


def get_history_provider() -> TicketHistoryProvider:
    """Selector for the active ticket-history provider.

    Returns :class:`NoopTicketHistory` today. CM-31 swaps in a Cosmos-backed
    provider here (env-gated on the tickets-container being provisioned),
    keeping the Triage node unchanged — the same indirection CM-28 uses for
    ``get_checkpointer()`` and CM-30 uses for ``get_triage_classifier()``.
    """
    return NoopTicketHistory()
