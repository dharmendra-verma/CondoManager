"""CM-55 follow-up — Cosmos-backed tenant directory for web chat login.

Covers the env-gated selector (offline → hardcoded), the document projection
(``_to_tenant``, incl. rejecting the seed "building" record), the Cosmos query
path with a fake container, and the Cosmos-first / hardcoded-fallback order in
:func:`lookup_tenant`.
"""

from __future__ import annotations

from typing import Any

import pytest
from agents.webchat import directory
from agents.webchat.tenants import TestTenant as _TestTenant


@pytest.fixture(autouse=True)
def _reset_directory() -> Any:
    """Each test starts with no cached directory and no COSMOS_ENDPOINT."""
    directory._reset_for_tests()
    yield
    directory._reset_for_tests()


# --- _to_tenant projection ---------------------------------------------------


def test_to_tenant_maps_admin_record() -> None:
    doc = {
        "id": "TEN-abc",
        "name": "Priya Nair",
        "unit": "9C",
        "mobile": "+91 98765-00000",  # stored formatting is normalized out
        "email": "priya@example.com",
    }
    tenant = directory._to_tenant(doc)
    assert tenant == _TestTenant(
        tenant_id="TEN-abc", name="Priya Nair", unit="9C", mobile="+919876500000"
    )


def test_to_tenant_rejects_building_record() -> None:
    # The seed-cosmos.py "building" tenant has no mobile/unit and must never
    # resolve as a login.
    doc = {"id": "tenant-smoke-test", "name": "Smoke Test Building", "unitCount": 10}
    assert directory._to_tenant(doc) is None


# --- selector gating ---------------------------------------------------------


def test_offline_falls_back_to_hardcoded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    tenant = directory.lookup_tenant("+91 98765-43210")
    assert tenant is not None
    assert tenant.tenant_id == "condo-tower-a"
    assert tenant.name == "Asha Rao"


def test_placeholder_endpoint_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSMOS_ENDPOINT", "REPLACE-ME")
    assert directory._get_directory() is None


# --- Cosmos query path -------------------------------------------------------


class _FakeContainer:
    """Emulates the ``tenants`` container: filters rows by the @mobile param."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def query_items(self, *, query: str, parameters: list[dict[str, Any]]) -> list[dict[str, Any]]:
        wanted = parameters[0]["value"]
        return [r for r in self._rows if r.get("mobile") == wanted]


def _directory_with(rows: list[dict[str, Any]]) -> directory.CosmosTenantDirectory:
    # Bypass __init__ (which would build a real CosmosClient) and inject a fake.
    d = directory.CosmosTenantDirectory.__new__(directory.CosmosTenantDirectory)
    d._container = _FakeContainer(rows)  # type: ignore[attr-defined]
    return d


def test_cosmos_lookup_hit() -> None:
    d = _directory_with(
        [{"id": "TEN-1", "name": "Priya Nair", "unit": "9C", "mobile": "+919876500000"}]
    )
    tenant = d.lookup("+91 98765-00000")
    assert tenant is not None and tenant.tenant_id == "TEN-1"


def test_cosmos_lookup_miss() -> None:
    d = _directory_with([])
    assert d.lookup("+919999999999") is None


def test_cosmos_query_failure_degrades_to_none() -> None:
    class _Boom:
        def query_items(self, **_: Any) -> list[dict[str, Any]]:
            raise RuntimeError("cosmos down")

    d = directory.CosmosTenantDirectory.__new__(directory.CosmosTenantDirectory)
    d._container = _Boom()  # type: ignore[attr-defined]
    assert d.lookup("+919876500000") is None


# --- lookup_tenant order (Cosmos first, hardcoded fallback) ------------------


def test_lookup_prefers_cosmos(monkeypatch: pytest.MonkeyPatch) -> None:
    cosmos_tenant = _TestTenant(
        tenant_id="TEN-9", name="Priya Nair", unit="9C", mobile="+919876500000"
    )
    monkeypatch.setattr(
        directory, "_get_directory", lambda: _StubDir({"+919876500000": cosmos_tenant})
    )
    assert directory.lookup_tenant("+919876500000") is cosmos_tenant


def test_lookup_falls_back_to_hardcoded_on_cosmos_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    # Cosmos configured but the number isn't there → the baked-in demo tenant
    # still resolves.
    monkeypatch.setattr(directory, "_get_directory", lambda: _StubDir({}))
    tenant = directory.lookup_tenant("+919876543210")
    assert tenant is not None and tenant.name == "Asha Rao"


def test_lookup_unknown_number_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(directory, "_get_directory", lambda: _StubDir({}))
    assert directory.lookup_tenant("+10000000000") is None


class _StubDir:
    def __init__(self, by_mobile: dict[str, _TestTenant]) -> None:
        self._by_mobile = by_mobile

    def lookup(self, mobile: str) -> _TestTenant | None:
        from agents.webchat.tenants import normalize_mobile

        return self._by_mobile.get(normalize_mobile(mobile))
