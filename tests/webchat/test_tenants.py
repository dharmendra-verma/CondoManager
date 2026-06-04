"""CM-55 — mobile-number -> test-tenant lookup (hit + miss)."""

from __future__ import annotations

from agents.webchat.tenants import TEST_TENANTS, lookup_tenant, normalize_mobile


def test_lookup_hit_canonical() -> None:
    mobile = "+919876543210"
    tenant = lookup_tenant(mobile)
    assert tenant is not None
    assert tenant.mobile == mobile
    assert tenant.unit == "4B"
    assert tenant.tenant_id == "condo-tower-a"


def test_lookup_hit_ignores_spaces_and_dashes() -> None:
    # Formatting must not change identity — resolves to the same tenant.
    tenant = lookup_tenant("+91 98765-43210")
    assert tenant is not None
    assert tenant.unit == "4B"


def test_lookup_miss_returns_none() -> None:
    assert lookup_tenant("+10000000000") is None


def test_normalize_mobile_strips_spaces_and_dashes() -> None:
    assert normalize_mobile("+91 98765-43210") == "+919876543210"


def test_test_tenants_keys_are_canonical() -> None:
    # Every map key must already be in canonical form so lookups match.
    for key in TEST_TENANTS:
        assert normalize_mobile(key) == key
