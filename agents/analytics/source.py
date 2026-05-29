"""Analytics data source seam (CM-36).

Reads tickets from the CM-31 ``tickets`` container over a date window. Mirrors
the env-gated selector pattern of ``get_ticket_repository`` / ``get_checkpointer``:
``CosmosAnalyticsSource`` when ``COSMOS_ENDPOINT`` is real, else a seeded /
empty ``InMemoryAnalyticsSource`` for tests + offline runs.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from .models import AnalyticsTicket

_log = logging.getLogger(__name__)

DATABASE_NAME = "condomanager"
CONTAINER_NAME = "tickets"
SECRET_PLACEHOLDER = "REPLACE-ME"


@runtime_checkable
class AnalyticsSource(Protocol):
    """Read surface the analytics engine needs over historical tickets."""

    def list_tickets(self, *, since: datetime) -> list[AnalyticsTicket]:
        """Return tickets created at/after ``since`` (all tenants)."""
        ...


class InMemoryAnalyticsSource:
    """Process-local source for tests + offline runs."""

    def __init__(self, tickets: list[AnalyticsTicket] | None = None) -> None:
        self._tickets = tickets or []

    def list_tickets(self, *, since: datetime) -> list[AnalyticsTicket]:
        return [t for t in self._tickets if t.created_at >= since]


class CosmosAnalyticsSource:
    """Cross-partition reader over the CM-31 ``tickets`` container."""

    def __init__(
        self,
        *,
        endpoint: str,
        database_name: str = DATABASE_NAME,
        container_name: str = CONTAINER_NAME,
    ) -> None:
        from azure.cosmos import CosmosClient  # noqa: PLC0415
        from azure.identity import DefaultAzureCredential  # noqa: PLC0415

        credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        self._client = CosmosClient(url=endpoint, credential=credential)
        self._container = self._client.get_database_client(database_name).get_container_client(
            container_name
        )

    def list_tickets(self, *, since: datetime) -> list[AnalyticsTicket]:
        # Cross-partition, bounded by the date window (weekly cadence keeps RU
        # cost modest). enable_cross_partition_query is implied by omitting the
        # partition_key on this SDK version.
        query = "SELECT * FROM c WHERE c.created_at >= @since"
        params: list[dict[str, object]] = [{"name": "@since", "value": since.isoformat()}]
        try:
            rows = list(self._container.query_items(query=query, parameters=params))
        except Exception as e:  # noqa: BLE001
            _log.warning("analytics list_tickets Cosmos query failed: %s", e)
            return []
        return [_from_doc(r) for r in rows]


def _from_doc(doc: dict[str, Any]) -> AnalyticsTicket:
    """Map a ticket Cosmos doc to an :class:`AnalyticsTicket`.

    Reads optional analytics fields (``vendor_id`` / ``tone`` / ``resolved_at``)
    when present; ``model_validate`` ignores the doc's extra keys.
    """
    return AnalyticsTicket.model_validate(doc)


_cached: AnalyticsSource | None = None


def get_analytics_source() -> AnalyticsSource:
    """Return the configured source (cached). Offline default: empty in-memory."""
    global _cached
    if _cached is not None:
        return _cached
    endpoint = os.environ.get("COSMOS_ENDPOINT", "").strip()
    if not endpoint or endpoint == SECRET_PLACEHOLDER:
        _log.info("COSMOS_ENDPOINT not set; analytics uses an empty in-memory source")
        _cached = InMemoryAnalyticsSource()
    else:
        _cached = CosmosAnalyticsSource(endpoint=endpoint)
    return _cached


def _reset_for_tests() -> None:
    global _cached
    _cached = None
