"""TEST-ONLY hardcoded tenant credentials for the CM-55 web chat harness.

╔══════════════════════════════════════════════════════════════════════════╗
║  TEST-ONLY — DO NOT SHIP / DO NOT USE IN PRODUCTION.                       ║
║                                                                            ║
║  Fake ``mobile -> tenant`` mappings for the self-contained web chat test   ║
║  channel (CM-55). There is NO OTP and NO real auth by design — the story   ║
║  explicitly allows hardcoded creds for a throwaway test channel. The whole ║
║  channel is gated behind ``WEBCHAT_TEST_ENABLED`` (see ``flag.py``), so    ║
║  this module is only ever reached in dev/test.                             ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TestTenant:
    """A hardcoded test tenant. ``mobile`` is the canonical E.164 key."""

    tenant_id: str
    name: str
    unit: str
    mobile: str


def normalize_mobile(raw: str) -> str:
    """Strip spaces and dashes so formatting doesn't change identity.

    Kept in lock-step with the frontend's ``normalizeMobile`` (portal
    ``src/test-chat.ts`` / ``src/admin.ts``) so client + server agree on the
    canonical form before a lookup.
    """
    return "".join(ch for ch in raw if not (ch.isspace() or ch == "-"))


# Keys MUST be the canonical (normalized) E.164 form — see ``normalize_mobile``.
TEST_TENANTS: dict[str, TestTenant] = {
    "+919876543210": TestTenant(
        tenant_id="condo-tower-a", name="Asha Rao", unit="4B", mobile="+919876543210"
    ),
    "+919812345678": TestTenant(
        tenant_id="condo-tower-a", name="Vikram Singh", unit="2A", mobile="+919812345678"
    ),
    "+14155550100": TestTenant(
        tenant_id="condo-bay-view", name="Jordan Lee", unit="12C", mobile="+14155550100"
    ),
}


def lookup_tenant(mobile: str) -> TestTenant | None:
    """Resolve a (possibly formatted) mobile number to a test tenant, or None."""
    return TEST_TENANTS.get(normalize_mobile(mobile))
