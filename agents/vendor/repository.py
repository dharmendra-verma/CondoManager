"""Vendor store seam (CM-35).

Follows the env-gated selector shape of ``get_ticket_repository`` (CM-31) and
``get_checkpointer`` (CM-28), but there is **no `vendors` Cosmos container yet**
(a deferred infra follow-up), so the only implementation today is the seeded
in-memory store. The selector + Protocol are in place so a Cosmos-backed store
drops in later with no caller changes.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .schema import Vendor
from .seed import seed_vendors


@runtime_checkable
class VendorRepository(Protocol):
    """Read surface the Vendor Agent needs over the contractor roster."""

    def list_vendors(self) -> list[Vendor]:
        """Return all known vendors."""
        ...


class InMemoryVendorRepository:
    """Process-local roster, seeded from :func:`seed_vendors` by default."""

    def __init__(self, vendors: list[Vendor] | None = None) -> None:
        self._vendors = vendors if vendors is not None else seed_vendors()

    def list_vendors(self) -> list[Vendor]:
        return list(self._vendors)


_cached: VendorRepository | None = None


def get_vendor_repository() -> VendorRepository:
    """Return the cached vendor repository (seeded in-memory for now).

    A Cosmos-backed ``vendors`` store is deferred; until it lands this always
    returns the seeded in-memory roster.
    """
    global _cached
    if _cached is None:
        _cached = InMemoryVendorRepository()
    return _cached


def _reset_for_tests() -> None:
    """Drop the cached repository so each test starts clean."""
    global _cached
    _cached = None
