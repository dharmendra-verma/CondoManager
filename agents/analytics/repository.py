"""I/O seams for the Analytics Agent (CM-36).

Jira: CM-36  | Epic: CM-11 (Agent 7 — Analytics & Forecasting)  | Phase 3

Three seams, all env-gated like CM-28's ``get_checkpointer`` / CM-34's
``get_vector_store`` so the package imports + tests run offline:

* :class:`AnalyticsReader` — read-only over the ``tickets`` (CM-31) and
  ``escalations`` (CM-32) containers. Does NOT touch CM-31's repository.
* :class:`DigestStore` — upserts a :class:`WeeklyDigest` to the ``digests``
  container (CM-36) for the CM-37 portal to render.
* :class:`DigestNotifier` — posts the rendered digest text to Slack. A text
  notifier (not the escalation-record ``ManagerNotifier``) so it carries an
  arbitrary digest body without fabricating an ``EscalationRecord``.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from agents.maintenance.schema import Ticket

from .models import (
    DATABASE_NAME,
    DIGESTS_CONTAINER,
    ESCALATIONS_CONTAINER,
    SECRET_PLACEHOLDER,
    TICKETS_CONTAINER,
    EscalationEvent,
    WeeklyDigest,
)

_log = logging.getLogger(__name__)

#: Slack webhook POST timeout (seconds) — short so a hung webhook can't stall
#: the weekly job; failure falls through to a logged miss.
_TIMEOUT_S = 5.0


# --- reader ------------------------------------------------------------------


@runtime_checkable
class AnalyticsReader(Protocol):
    """Reads the inputs the analyzers need, scoped to a tenant + window."""

    def recent_tickets(self, tenant_id: str, *, since: datetime) -> list[Ticket]: ...

    def recent_escalation_events(
        self, tenant_id: str, *, since: datetime
    ) -> list[EscalationEvent]: ...


class InMemoryAnalyticsReader:
    """Process-local reader for tests + the offline path."""

    def __init__(
        self,
        tickets: list[Ticket] | None = None,
        events: list[EscalationEvent] | None = None,
    ) -> None:
        self._tickets = tickets or []
        self._events = events or []

    def recent_tickets(self, tenant_id: str, *, since: datetime) -> list[Ticket]:
        return [
            t
            for t in self._tickets
            if t.tenant_id == tenant_id and t.created_at >= since
        ]

    def recent_escalation_events(
        self, tenant_id: str, *, since: datetime
    ) -> list[EscalationEvent]:
        return [e for e in self._events if e.ts >= since]


class CosmosAnalyticsReader:
    """Read-only Cosmos reader over ``tickets`` + ``escalations``."""

    def __init__(self, *, endpoint: str, database_name: str = DATABASE_NAME) -> None:
        from azure.cosmos import CosmosClient  # noqa: PLC0415
        from azure.identity import DefaultAzureCredential  # noqa: PLC0415

        credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        database = CosmosClient(url=endpoint, credential=credential).get_database_client(
            database_name
        )
        self._tickets = database.get_container_client(TICKETS_CONTAINER)
        self._escalations = database.get_container_client(ESCALATIONS_CONTAINER)

    def recent_tickets(self, tenant_id: str, *, since: datetime) -> list[Ticket]:
        query = (
            "SELECT * FROM c WHERE c.tenantId = @tid AND c.created_at >= @since"
        )
        params: list[dict[str, object]] = [
            {"name": "@tid", "value": tenant_id},
            {"name": "@since", "value": since.isoformat()},
        ]
        try:
            rows = list(
                self._tickets.query_items(
                    query=query, parameters=params, partition_key=tenant_id
                )
            )
        except Exception as e:  # noqa: BLE001  (a read failure must not abort the run)
            _log.warning("recent_tickets query failed: %s", e)
            return []
        return [Ticket.model_validate(r) for r in rows]

    def recent_escalation_events(
        self, tenant_id: str, *, since: datetime
    ) -> list[EscalationEvent]:
        # EscalationRecord carries no timestamp, so filter on the Cosmos
        # server write time `_ts` (epoch seconds) and project it onto the event.
        query = "SELECT * FROM c WHERE c.tenantId = @tid AND c._ts >= @since"
        params: list[dict[str, object]] = [
            {"name": "@tid", "value": tenant_id},
            {"name": "@since", "value": int(since.timestamp())},
        ]
        try:
            rows = list(
                self._escalations.query_items(
                    query=query, parameters=params, partition_key=tenant_id
                )
            )
        except Exception as e:  # noqa: BLE001
            _log.warning("recent_escalation_events query failed: %s", e)
            return []
        return [
            EscalationEvent(
                ts=datetime.fromtimestamp(int(r["_ts"]), UTC),
                severity=str(r.get("severity", "high")),
                legal_risk=bool(r.get("legal_risk", False)),
                category=str(r.get("category", "")),
            )
            for r in rows
        ]


def get_analytics_reader() -> AnalyticsReader | None:
    """Cosmos reader when ``COSMOS_ENDPOINT`` is real, else ``None`` (job skips)."""
    endpoint = os.environ.get("COSMOS_ENDPOINT", "").strip()
    if not endpoint or endpoint == SECRET_PLACEHOLDER:
        _log.info("COSMOS_ENDPOINT not set; analytics reader unavailable.")
        return None
    return CosmosAnalyticsReader(endpoint=endpoint)


# --- digest store ------------------------------------------------------------


@runtime_checkable
class DigestStore(Protocol):
    """Persists weekly digests for the tenant portal (CM-37)."""

    def save(self, digest: WeeklyDigest) -> None: ...

    def latest(self, tenant_id: str) -> WeeklyDigest | None: ...


class NoopDigestStore:
    """In-memory digest store — offline/tests fallback."""

    def __init__(self) -> None:
        self._by_tenant: dict[str, list[WeeklyDigest]] = {}

    def save(self, digest: WeeklyDigest) -> None:
        self._by_tenant.setdefault(digest.tenant_id, []).append(digest)

    def latest(self, tenant_id: str) -> WeeklyDigest | None:
        items = self._by_tenant.get(tenant_id, [])
        return items[-1] if items else None


class CosmosDigestStore:
    """Cosmos-backed digest store (``digests`` container, partition /tenantId)."""

    def __init__(self, *, endpoint: str, database_name: str = DATABASE_NAME) -> None:
        from azure.cosmos import CosmosClient  # noqa: PLC0415
        from azure.identity import DefaultAzureCredential  # noqa: PLC0415

        credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        self._container = (
            CosmosClient(url=endpoint, credential=credential)
            .get_database_client(database_name)
            .get_container_client(DIGESTS_CONTAINER)
        )

    def save(self, digest: WeeklyDigest) -> None:
        self._container.upsert_item(digest.to_cosmos())

    def latest(self, tenant_id: str) -> WeeklyDigest | None:
        query = (
            "SELECT TOP 1 * FROM c WHERE c.tenantId = @tid ORDER BY c.week_start DESC"
        )
        params: list[dict[str, object]] = [{"name": "@tid", "value": tenant_id}]
        try:
            rows = list(
                self._container.query_items(
                    query=query, parameters=params, partition_key=tenant_id
                )
            )
        except Exception as e:  # noqa: BLE001
            _log.warning("digest latest() query failed: %s", e)
            return None
        return WeeklyDigest.from_cosmos(rows[0]) if rows else None


def get_digest_store() -> DigestStore | None:
    """Cosmos store when ``COSMOS_ENDPOINT`` is real, else ``None`` (job skips)."""
    endpoint = os.environ.get("COSMOS_ENDPOINT", "").strip()
    if not endpoint or endpoint == SECRET_PLACEHOLDER:
        _log.info("COSMOS_ENDPOINT not set; digest store unavailable.")
        return None
    return CosmosDigestStore(endpoint=endpoint)


# --- notifier ----------------------------------------------------------------


@runtime_checkable
class DigestNotifier(Protocol):
    """Posts the rendered digest text. Returns delivery success; never raises."""

    def send(self, text: str) -> bool: ...


class LogDigestNotifier:
    """Logs the digest. Offline / no-webhook fallback (always succeeds)."""

    def send(self, text: str) -> bool:
        _log.info("WEEKLY DIGEST (no webhook configured)\n%s", text)
        return True


class SlackDigestNotifier:
    """Posts the digest text to a Slack incoming webhook via httpx."""

    def __init__(self, webhook_url: str) -> None:
        self._url = webhook_url

    def send(self, text: str) -> bool:
        import httpx  # noqa: PLC0415  (lazy; LogDigestNotifier path skips it)

        try:
            resp = httpx.post(self._url, json={"text": text}, timeout=_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001  (a flaky webhook must not fail the job)
            _log.warning("Slack digest POST failed: %s", e)
            return False
        if resp.status_code >= 400:
            _log.warning("Slack digest POST returned %s", resp.status_code)
            return False
        return True


def get_digest_notifier() -> DigestNotifier:
    """Slack when ``SLACK_WEBHOOK_URL`` is real, else ``LogDigestNotifier``."""
    url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if url and url != SECRET_PLACEHOLDER:
        return SlackDigestNotifier(url)
    return LogDigestNotifier()
