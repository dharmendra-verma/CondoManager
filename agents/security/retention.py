"""Data retention + right-to-erasure (CM-38 AC6).

Two halves:

* :data:`RETENTION_POLICY` — the declarative source of truth for how long each
  Cosmos container keeps data. It mirrors the ``defaultTtl`` values set in
  ``infra/bicep/modules/cosmos.bicep`` so policy and infra can be diff-checked
  (the Bicep lint test asserts the audit container is ``-1``/never-expire). A
  value of ``None`` means "retain indefinitely" (``defaultTtl: -1``).
* :func:`delete_tenant_data` — the GDPR/SOC2 right-to-erasure routine. It fans
  out over a set of :class:`ErasableSource` objects (one per container that
  holds tenant data), deleting everything for a tenant and returning a
  :class:`DeletionReport`. It is **non-blocking**: a failure deleting from one
  source is recorded and the rest still run, so a single container outage can't
  leave erasure half-done with no record of what remains.

The concrete Cosmos-backed sources are wired by each owning package's
repository (they already know their container + partition key); this module
defines the protocol + the orchestration so erasure is uniform and testable.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

_log = logging.getLogger(__name__)

# Container -> retention in days. None = retain indefinitely (defaultTtl: -1).
# Keep in sync with infra/bicep/modules/cosmos.bicep defaultTtl values.
RETENTION_POLICY: dict[str, int | None] = {
    "tenants": None,          # master records — retained until tenant erasure
    "tickets": None,          # operational history — retained until erasure
    "conversations": None,    # message history — retained until erasure
    "policies-vector": None,  # knowledge base — not tenant PII
    "checkpoints": 30,        # LangGraph state — auto-purged (CM-28)
    "escalations": None,      # legal/compliance surface — never auto-purge (CM-32)
    "knowledge_sync": None,   # sync cursor — operational
    "digests": 90,            # rolling rebuildable view — auto-purged (CM-36)
    "audit": None,            # immutable audit trail — never expires (CM-38 AC4)
}


@runtime_checkable
class ErasableSource(Protocol):
    """A data store that can erase everything for a single tenant (AC6)."""

    @property
    def name(self) -> str:
        """Short identifier (usually the container name) for the report."""
        ...

    def delete_for_tenant(self, tenant_id: str) -> int:
        """Delete all records for ``tenant_id``; return the count deleted."""
        ...


class SourceDeletion(BaseModel):
    """Per-source outcome within a :class:`DeletionReport`."""

    source: str
    deleted: int = 0
    ok: bool = True
    error: str | None = None


class DeletionReport(BaseModel):
    """Outcome of a :func:`delete_tenant_data` run."""

    tenant_id: str
    results: list[SourceDeletion] = []

    @property
    def total_deleted(self) -> int:
        return sum(r.deleted for r in self.results)

    @property
    def complete(self) -> bool:
        """True only if every source erased without error."""
        return all(r.ok for r in self.results)


def delete_tenant_data(
    tenant_id: str,
    *,
    sources: Iterable[ErasableSource],
) -> DeletionReport:
    """Erase all data for ``tenant_id`` across ``sources``. Non-blocking.

    Each source is attempted independently; a failure is captured in the
    report (``ok=False`` + ``error``) and the remaining sources still run. The
    caller inspects :attr:`DeletionReport.complete` to decide whether to retry
    the failed sources — erasure is not "done" until every source reports ``ok``.
    """
    report = DeletionReport(tenant_id=tenant_id, results=[])
    for source in sources:
        try:
            count = source.delete_for_tenant(tenant_id)
            report.results.append(SourceDeletion(source=source.name, deleted=count, ok=True))
        except Exception as e:  # noqa: BLE001 — one source failing must not abort the rest
            _log.error("erasure failed for tenant=%s source=%s: %s", tenant_id, source.name, e)
            report.results.append(
                SourceDeletion(source=source.name, deleted=0, ok=False, error=str(e))
            )
    return report
