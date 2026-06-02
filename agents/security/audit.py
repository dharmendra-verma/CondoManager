"""Append-only audit log seam (CM-38 AC4).

The seam mirrors ``get_ticket_repository`` / ``get_analytics_source``:

* :class:`InMemoryAuditSink` — process-local, the offline/test default.
* :class:`CosmosAuditSink` — writes to the dedicated ``audit`` Cosmos
  container (``defaultTtl: -1`` — never expires; partition ``/tenantId``).
  ``azure.cosmos`` is lazy-imported.

**Immutable retention.** Cosmos has no native WORM. Immutability is approximated
by three layered controls (documented in ``docs/SECURITY.md``):

1. :class:`~agents.security.models.AuditEvent` is ``frozen`` — no in-place
   mutation in code.
2. This sink exposes **only** ``record`` — there is no update or delete method,
   and ``CosmosAuditSink`` uses ``create_item`` (not ``upsert``) so a replay of
   the same id is rejected rather than silently overwriting.
3. The ``audit`` container never expires and rides Cosmos continuous backup.

``record_audit(...)`` is the ergonomic entry point: it masks the free-text ``detail``
(CM-27 ``mask_text``) and stamps id/timestamp before recording, so callers
can't accidentally write raw PII into the trail.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from .masking import mask_text
from .models import AuditAction, AuditEvent

_log = logging.getLogger(__name__)

DATABASE_NAME = "condomanager"
CONTAINER_NAME = "audit"
SECRET_PLACEHOLDER = "REPLACE-ME"


@runtime_checkable
class AuditSink(Protocol):
    """Append-only audit destination. Intentionally write-only (no read/update)."""

    def record(self, event: AuditEvent) -> None:
        """Persist one audit event. Must not mutate or overwrite prior events."""
        ...


class InMemoryAuditSink:
    """Process-local append-only sink for tests + offline runs."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self._events.append(event)

    @property
    def events(self) -> tuple[AuditEvent, ...]:
        """Read the recorded events (tuple = caller can't mutate the log)."""
        return tuple(self._events)


class CosmosAuditSink:
    """Cosmos-backed append-only sink over the ``audit`` container."""

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

    def record(self, event: AuditEvent) -> None:
        # create_item (not upsert): an id replay must fail, never overwrite an
        # existing audit record. The write failing is preferable to a silent
        # tamper, so we log loudly but do NOT swallow into success.
        try:
            self._container.create_item(event.to_doc())
        except Exception as e:  # noqa: BLE001
            _log.error("audit record write failed for id=%s: %s", event.id, e)
            raise


_cached: AuditSink | None = None


def get_audit_sink() -> AuditSink:
    """Return the configured audit sink (cached). Offline default: in-memory.

    ``COSMOS_ENDPOINT`` unset / blank / ``REPLACE-ME`` -> in-memory sink;
    otherwise the Cosmos-backed sink over the ``audit`` container.
    """
    global _cached
    if _cached is not None:
        return _cached
    endpoint = os.environ.get("COSMOS_ENDPOINT", "").strip()
    if not endpoint or endpoint == SECRET_PLACEHOLDER:
        _log.info("COSMOS_ENDPOINT not set; using InMemoryAuditSink (audit log is process-local)")
        _cached = InMemoryAuditSink()
    else:
        _cached = CosmosAuditSink(endpoint=endpoint)
    return _cached


def record_audit(
    action: AuditAction,
    *,
    actor: str,
    resource: str,
    tenant_id: str | None = None,
    detail: str = "",
    sink: AuditSink | None = None,
    now: datetime | None = None,
    event_id: str | None = None,
) -> AuditEvent:
    """Build, mask, and record an audit event in one call; return the event.

    ``detail`` is masked with CM-27 ``mask_text`` before the (frozen) event is
    constructed, so PII never reaches the trail in plaintext. ``now`` and
    ``event_id`` are injectable for deterministic tests; in production they
    default to ``datetime.now(UTC)`` and a uuid4.
    """
    event = AuditEvent(
        id=event_id or uuid.uuid4().hex,
        ts=now or datetime.now(UTC),
        action=action,
        actor=actor,
        resource=resource,
        tenant_id=tenant_id,
        detail=mask_text(detail),
    )
    (sink or get_audit_sink()).record(event)
    return event


def _reset_for_tests() -> None:
    """Drop the cached sink so each test starts clean."""
    global _cached
    _cached = None
