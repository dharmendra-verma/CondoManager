"""Retention policy + right-to-erasure tests (CM-38 AC6)."""

from __future__ import annotations

from agents.security.retention import (
    RETENTION_POLICY,
    delete_tenant_data,
)


class _FakeSource:
    """An in-memory ErasableSource for testing the fan-out."""

    def __init__(self, name: str, by_tenant: dict[str, int]) -> None:
        self._name = name
        self._by_tenant = by_tenant
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    def delete_for_tenant(self, tenant_id: str) -> int:
        self.calls.append(tenant_id)
        return self._by_tenant.get(tenant_id, 0)


class _ExplodingSource:
    @property
    def name(self) -> str:
        return "boom"

    def delete_for_tenant(self, tenant_id: str) -> int:
        raise RuntimeError("cosmos unavailable")


def test_policy_covers_every_live_container() -> None:
    # The policy table is the source of truth checked against cosmos.bicep TTLs.
    # Guard that the containers CM-17/28/32/34/36/38 created are all classified.
    expected = {
        "tenants",
        "tickets",
        "conversations",
        "policies-vector",
        "checkpoints",
        "escalations",
        "knowledge_sync",
        "digests",
        "audit",
    }
    assert expected.issubset(RETENTION_POLICY.keys())


def test_audit_and_escalations_never_expire() -> None:
    assert RETENTION_POLICY["audit"] is None
    assert RETENTION_POLICY["escalations"] is None


def test_rolling_views_have_finite_ttl() -> None:
    assert RETENTION_POLICY["checkpoints"] == 30
    assert RETENTION_POLICY["digests"] == 90


def test_delete_tenant_data_fans_out_and_counts() -> None:
    s1 = _FakeSource("tickets", {"t-1": 3})
    s2 = _FakeSource("conversations", {"t-1": 5})
    report = delete_tenant_data("t-1", sources=[s1, s2])

    assert s1.calls == ["t-1"]
    assert s2.calls == ["t-1"]
    assert report.total_deleted == 8
    assert report.complete is True


def test_delete_is_non_blocking_on_source_failure() -> None:
    good = _FakeSource("tickets", {"t-9": 2})
    bad = _ExplodingSource()
    after = _FakeSource("conversations", {"t-9": 4})

    report = delete_tenant_data("t-9", sources=[good, bad, after])

    # The failing source did not abort the run — `after` still executed.
    assert after.calls == ["t-9"]
    assert report.complete is False
    assert report.total_deleted == 6
    failed = [r for r in report.results if not r.ok]
    assert len(failed) == 1
    assert failed[0].source == "boom"
    assert "cosmos unavailable" in (failed[0].error or "")


def test_empty_sources_is_complete_and_zero() -> None:
    report = delete_tenant_data("t-1", sources=[])
    assert report.complete is True
    assert report.total_deleted == 0
