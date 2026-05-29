"""Ticket persistence seam (CM-31).

Mirrors the env-gated pattern of ``agents.orchestrator.checkpointer`` exactly:

* :class:`CosmosTicketRepository` — real, backed by the CM-17 ``tickets``
  container (partition key ``/tenantId``), auth via ``DefaultAzureCredential``
  (the CM-18 User-Assigned MI in prod, ``az login`` locally). ``azure.cosmos``
  is lazy-imported so the in-memory path pays no import cost.
* :class:`InMemoryTicketRepository` — process-local store for tests + the
  offline demo.
* :func:`get_ticket_repository` — returns Cosmos when ``COSMOS_ENDPOINT`` is a
  real value, else a cached in-memory singleton (so dedup persists across
  graph invocations within one process, the way ``MemorySaver`` does for
  checkpoints).

The domain model (:class:`Ticket`) is snake_case; the Cosmos document uses
``tenantId`` for the partition key. ``_to_doc`` / ``_from_doc`` bridge them.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from .schema import Ticket

_log = logging.getLogger(__name__)

DATABASE_NAME = "condomanager"
CONTAINER_NAME = "tickets"
SECRET_PLACEHOLDER = "REPLACE-ME"


@runtime_checkable
class TicketRepository(Protocol):
    """Minimal persistence surface the Maintenance Agent needs."""

    def add(self, ticket: Ticket) -> None:
        """Persist a new ticket."""
        ...

    def recent_for_unit(self, tenant_id: str, unit: str, *, since: datetime) -> list[Ticket]:
        """Return tickets for ``(tenant_id, unit)`` created at/after ``since``."""
        ...


class InMemoryTicketRepository:
    """Process-local ticket store for tests and the offline demo."""

    def __init__(self) -> None:
        self._by_tenant: dict[str, list[Ticket]] = {}

    def add(self, ticket: Ticket) -> None:
        self._by_tenant.setdefault(ticket.tenant_id, []).append(ticket)

    def recent_for_unit(self, tenant_id: str, unit: str, *, since: datetime) -> list[Ticket]:
        return [
            t
            for t in self._by_tenant.get(tenant_id, [])
            if t.unit == unit and t.created_at >= since
        ]


class CosmosTicketRepository:
    """Cosmos-backed repository over the CM-17 ``tickets`` container."""

    def __init__(
        self,
        *,
        endpoint: str,
        database_name: str = DATABASE_NAME,
        container_name: str = CONTAINER_NAME,
    ) -> None:
        # Lazy import so MemorySaver/InMemory processes don't pay the cost.
        from azure.cosmos import CosmosClient  # noqa: PLC0415
        from azure.identity import DefaultAzureCredential  # noqa: PLC0415

        credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        self._client = CosmosClient(url=endpoint, credential=credential)
        self._container = self._client.get_database_client(database_name).get_container_client(
            container_name
        )

    def add(self, ticket: Ticket) -> None:
        self._container.upsert_item(_to_doc(ticket))

    def recent_for_unit(self, tenant_id: str, unit: str, *, since: datetime) -> list[Ticket]:
        # Single-partition query (partition_key=tenant_id) bounded by the
        # 7-day cutoff — the consistent index on /* serves the predicate.
        query = (
            "SELECT * FROM c WHERE c.tenantId = @tid AND c.unit = @unit "
            "AND c.created_at >= @since"
        )
        params: list[dict[str, object]] = [
            {"name": "@tid", "value": tenant_id},
            {"name": "@unit", "value": unit},
            {"name": "@since", "value": since.isoformat()},
        ]
        try:
            rows = list(
                self._container.query_items(query=query, parameters=params, partition_key=tenant_id)
            )
        except Exception as e:  # noqa: BLE001
            # A read failure must not block ticket creation; degrade to
            # "no known duplicates" and log loudly rather than 500 the tenant.
            _log.warning("recent_for_unit Cosmos query failed: %s", e)
            return []
        return [_from_doc(r) for r in rows]


def _to_doc(ticket: Ticket) -> dict[str, Any]:
    """Map a :class:`Ticket` to its Cosmos document (snake -> ``tenantId``)."""
    doc = ticket.model_dump(mode="json")
    doc["tenantId"] = ticket.tenant_id  # partition-key path /tenantId
    return doc


def _from_doc(doc: dict[str, Any]) -> Ticket:
    """Map a Cosmos document back to a :class:`Ticket`.

    Tolerates the extra ``tenantId`` (and Cosmos system fields) that aren't
    model fields — Pydantic ignores unknown keys by default here.
    """
    return Ticket.model_validate(doc)


# --- selector (cached singletons, resettable for tests) ---------------------

_cached: TicketRepository | None = None


def get_ticket_repository() -> TicketRepository:
    """Return the configured repository, cached for the process.

    ``COSMOS_ENDPOINT`` unset / blank / ``REPLACE-ME`` -> in-memory singleton;
    otherwise a Cosmos-backed repository. Caching the in-memory instance lets
    dedup persist across graph invocations within one process.
    """
    global _cached
    if _cached is not None:
        return _cached
    endpoint = os.environ.get("COSMOS_ENDPOINT", "").strip()
    if not endpoint or endpoint == SECRET_PLACEHOLDER:
        _log.info(
            "COSMOS_ENDPOINT not set; using InMemoryTicketRepository "
            "(tickets will not survive process restart)"
        )
        _cached = InMemoryTicketRepository()
    else:
        _cached = CosmosTicketRepository(endpoint=endpoint)
    return _cached


def _reset_for_tests() -> None:
    """Drop the cached repository so each test starts clean."""
    global _cached
    _cached = None
