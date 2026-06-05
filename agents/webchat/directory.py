"""Cosmos-backed tenant directory for the web chat channel (CM-55 follow-up).

The original CM-55 channel resolved a login against the hardcoded
:data:`agents.webchat.tenants.TEST_TENANTS` map only. This module upgrades the
lookup to consult the **live ``tenants`` Cosmos container** — the same master
record the admin page manages (``portal/api/src/tenantRepo.ts``) — so a tenant
added through the admin UI can log in to the web chat.

It mirrors the env-gated repository pattern used across the agents package
(see :mod:`agents.maintenance.repository`): ``COSMOS_ENDPOINT`` +
``DefaultAzureCredential`` (the CM-18 shared MI in prod, ``az login`` locally),
``azure.cosmos`` lazy-imported so the offline path pays no import cost, and a
cached singleton selected by the env var.

Lookup order (see :func:`lookup_tenant`):

1. **Cosmos** when ``COSMOS_ENDPOINT`` is configured — query the ``tenants``
   container by normalized ``mobile``.
2. **Hardcoded demo numbers** (:func:`agents.webchat.tenants.lookup_tenant`) on
   a Cosmos miss *or* when Cosmos is unset — so the three CM-55 demo tenants and
   the offline/test path keep working unchanged.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .tenants import TestTenant, normalize_mobile
from .tenants import lookup_tenant as _lookup_hardcoded

_log = logging.getLogger(__name__)

DATABASE_NAME = "condomanager"
CONTAINER_NAME = "tenants"
# Same CM-18 placeholder convention used everywhere else: an unseeded secret
# ships as the literal "REPLACE-ME" and must be treated as unconfigured.
SECRET_PLACEHOLDER = "REPLACE-ME"


def _cosmos_endpoint() -> str | None:
    """Return a real ``COSMOS_ENDPOINT`` or ``None`` when unset/placeholder."""
    endpoint = os.environ.get("COSMOS_ENDPOINT", "").strip()
    if not endpoint or endpoint == SECRET_PLACEHOLDER:
        return None
    return endpoint


def _to_tenant(doc: dict[str, Any]) -> TestTenant | None:
    """Project a raw ``tenants`` document into a :class:`TestTenant`.

    Returns ``None`` for any doc missing a required string field — notably the
    seed "building" record (``tenant-smoke-test``), which has no ``mobile`` /
    ``unit`` and so is never matched as a login. The Cosmos ``id`` (the admin
    ``TEN-<uuid>``) becomes ``tenant_id``: it is the only stable per-record
    identifier the admin schema carries, and downstream triage/ticketing
    tolerates any opaque partition value.
    """
    tenant_id = doc.get("id")
    name = doc.get("name")
    unit = doc.get("unit")
    mobile = doc.get("mobile")
    if not (
        isinstance(tenant_id, str)
        and isinstance(name, str)
        and isinstance(unit, str)
        and isinstance(mobile, str)
    ):
        return None
    return TestTenant(
        tenant_id=tenant_id,
        name=name,
        unit=unit,
        mobile=normalize_mobile(mobile),
    )


class CosmosTenantDirectory:
    """Resolve a mobile number against the ``tenants`` Cosmos container."""

    def __init__(
        self,
        *,
        endpoint: str,
        database_name: str = DATABASE_NAME,
        container_name: str = CONTAINER_NAME,
    ) -> None:
        # Lazy import so the offline/in-memory path doesn't pay the cost (and a
        # missing azure-cosmos degrades to the hardcoded directory upstream).
        from azure.cosmos import CosmosClient  # noqa: PLC0415
        from azure.identity import DefaultAzureCredential  # noqa: PLC0415

        credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        self._client = CosmosClient(url=endpoint, credential=credential)
        self._container = self._client.get_database_client(database_name).get_container_client(
            container_name
        )

    def lookup(self, mobile: str) -> TestTenant | None:
        """Return the tenant whose ``mobile`` matches, or ``None``.

        The ``tenants`` container is partitioned by ``/id``, so a lookup by
        ``mobile`` spans partitions. The sync azure-cosmos SDK does NOT enable
        that implicitly (unlike the JS SDK the admin API uses) — without
        ``enable_cross_partition_query=True`` it raises ``BadRequest``, which we
        would swallow into a silent ``unknown_number``. A genuine query failure
        still degrades to ``None`` (→ hardcoded fallback) rather than 500-ing.
        """
        key = normalize_mobile(mobile)
        try:
            rows = list(
                self._container.query_items(
                    query="SELECT * FROM c WHERE c.mobile = @mobile",
                    parameters=[{"name": "@mobile", "value": key}],
                    enable_cross_partition_query=True,
                )
            )
        except Exception as e:  # noqa: BLE001 -- a lookup failure must not 500 the login
            _log.warning("tenant directory Cosmos query failed: %s", e)
            return None
        for doc in rows:
            tenant = _to_tenant(doc)
            if tenant is not None:
                return tenant
        return None


_cached: CosmosTenantDirectory | None = None


def _get_directory() -> CosmosTenantDirectory | None:
    """Return the cached Cosmos directory, or ``None`` when unconfigured.

    A construction/import failure (e.g. azure-cosmos absent) logs and returns
    ``None`` so the caller falls back to the hardcoded directory instead of
    crashing the channel.
    """
    global _cached
    if _cached is not None:
        return _cached
    endpoint = _cosmos_endpoint()
    if endpoint is None:
        return None
    try:
        _cached = CosmosTenantDirectory(endpoint=endpoint)
    except Exception as e:  # noqa: BLE001 -- degrade to hardcoded rather than crash
        _log.warning("could not build Cosmos tenant directory: %s", e)
        return None
    return _cached


def lookup_tenant(mobile: str) -> TestTenant | None:
    """Resolve ``mobile`` to a tenant: Cosmos first, then the demo numbers.

    Drop-in replacement for :func:`agents.webchat.tenants.lookup_tenant` — same
    signature and return type — so :mod:`agents.webchat.service` only swaps the
    import.
    """
    directory = _get_directory()
    if directory is not None:
        tenant = directory.lookup(mobile)
        if tenant is not None:
            return tenant
    return _lookup_hardcoded(mobile)


def _reset_for_tests() -> None:
    """Drop the cached directory so the next call re-reads ``COSMOS_ENDPOINT``."""
    global _cached
    _cached = None
