"""Escalation-record store (CM-32 AC #3).

Jira: CM-32  | Epic: CM-7 (Agent 4 — Escalation Manager Agent)  | Phase 1

Persists :class:`~agents.orchestrator.state.EscalationRecord` to the Cosmos
``escalations`` container. A direct twin of CM-28's ``checkpointer.py``:
``DefaultAzureCredential`` (CM-18 User-Assigned MI in prod, ``az login`` in
dev), lazy ``azure.cosmos`` import, and an env-gated selector so the suite +
demo run with no Cosmos access.

* :class:`CosmosEscalationStore` — upserts each record (partition ``/tenantId``).
* :class:`NoopEscalationStore` — in-memory; offline/tests fallback.
* :func:`get_escalation_store` — ``CosmosEscalationStore`` when
  ``COSMOS_ENDPOINT`` is set to a real value, else ``NoopEscalationStore``.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

from .state import EscalationRecord

_log = logging.getLogger(__name__)

#: Cosmos DB database name from CM-17.
DATABASE_NAME = "condomanager"

#: Cosmos container added by this story's cosmos.bicep change.
CONTAINER_NAME = "escalations"

#: CM-18 placeholder; treated as if-unset.
SECRET_PLACEHOLDER = "REPLACE-ME"


@runtime_checkable
class EscalationStore(Protocol):
    """Persists escalation records and reads them back per tenant."""

    def save(self, record: EscalationRecord) -> None:
        """Durably store (upsert) ``record`` keyed by its ``record_id``."""
        ...

    def recent(self, tenant_id: str, *, limit: int = 10) -> list[EscalationRecord]:
        """Return up to ``limit`` recent records for ``tenant_id`` (newest first)."""
        ...

    def record_rating(self, record: EscalationRecord) -> None:
        """Persist a HITL-rated ``record`` (CM-44), replacing the prior version.

        ``record`` already carries the manager's decision (``status``) + optional
        ``manager_rating`` / ``rating_comment`` / ``rated_at`` set by
        ``hitl_review``. Idempotent by ``record_id`` so re-rating updates in place.
        """
        ...


class NoopEscalationStore:
    """In-memory store — the offline / no-Cosmos fallback.

    Keeps records in a process-local list so tests can assert ``save`` was
    called and ``recent`` reflects it. Not durable; production uses
    :class:`CosmosEscalationStore`.
    """

    def __init__(self) -> None:
        self._records: list[EscalationRecord] = []

    def save(self, record: EscalationRecord) -> None:
        self._records.append(record)
        _log.info(
            "escalation recorded (in-memory): id=%s tenant=%s category=%s legal_risk=%s",
            record.record_id,
            record.tenant_id,
            record.category.value,
            record.legal_risk,
        )

    def recent(self, tenant_id: str, *, limit: int = 10) -> list[EscalationRecord]:
        matches = [r for r in self._records if r.tenant_id == tenant_id]
        return list(reversed(matches))[:limit]

    def record_rating(self, record: EscalationRecord) -> None:
        # Replace any existing record with the same id (re-rating updates in
        # place) so ``recent()`` reflects the rating without a duplicate row.
        self._records = [r for r in self._records if r.record_id != record.record_id]
        self._records.append(record)
        _log.info(
            "escalation rated (in-memory): id=%s tenant=%s status=%s rating=%s",
            record.record_id,
            record.tenant_id,
            record.status,
            record.manager_rating,
        )


class CosmosEscalationStore:
    """Cosmos-backed store. Partition key ``/tenantId`` (see cosmos.bicep)."""

    def __init__(
        self,
        *,
        endpoint: str,
        database_name: str = DATABASE_NAME,
        container_name: str = CONTAINER_NAME,
    ) -> None:
        # Lazy-import so MemorySaver/Noop paths don't pay the azure SDK cost.
        from azure.cosmos import CosmosClient  # noqa: PLC0415
        from azure.identity import DefaultAzureCredential  # noqa: PLC0415

        credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        self._client = CosmosClient(url=endpoint, credential=credential)
        self._container = (
            self._client.get_database_client(database_name)
            .get_container_client(container_name)
        )

    def save(self, record: EscalationRecord) -> None:
        # ``id`` + ``tenantId`` (partition key) are added alongside the model
        # fields so the doc matches the container's /tenantId partition path.
        doc = {
            **record.model_dump(mode="json"),
            "id": record.record_id,
            "tenantId": record.tenant_id,
        }
        self._container.upsert_item(doc)

    def record_rating(self, record: EscalationRecord) -> None:
        # ``upsert_item`` is idempotent by ``id``, so persisting the rated record
        # updates the existing doc in place — no separate update path needed.
        self.save(record)

    def recent(self, tenant_id: str, *, limit: int = 10) -> list[EscalationRecord]:
        query = (
            f"SELECT TOP {int(limit)} * FROM c WHERE c.tenantId = @tid "
            "ORDER BY c._ts DESC"
        )
        params: list[dict[str, object]] = [{"name": "@tid", "value": tenant_id}]
        try:
            rows = list(
                self._container.query_items(
                    query=query, parameters=params, partition_key=tenant_id
                )
            )
        except Exception as e:  # noqa: BLE001
            _log.warning("escalation recent() query failed: %s", e)
            return []
        return [EscalationRecord.model_validate(r) for r in rows]


def get_escalation_store() -> EscalationStore:
    """Return ``CosmosEscalationStore`` if configured, else ``NoopEscalationStore``.

    Same env-gating as ``checkpointer.get_checkpointer`` — ``COSMOS_ENDPOINT``
    unset / blank / ``REPLACE-ME`` falls back to the in-memory store.
    """
    endpoint = os.environ.get("COSMOS_ENDPOINT", "").strip()
    if not endpoint or endpoint == SECRET_PLACEHOLDER:
        _log.info("COSMOS_ENDPOINT not set; escalation records kept in-memory only")
        return NoopEscalationStore()
    return CosmosEscalationStore(endpoint=endpoint)
