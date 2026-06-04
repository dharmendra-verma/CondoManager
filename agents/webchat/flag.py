"""Dev/test gate for the CM-55 web chat harness.

Reads ``WEBCHAT_TEST_ENABLED`` at **call time** (not import time) so a single env
var disables the whole channel in staging/prod, and tests can toggle it per-test
with ``monkeypatch.setenv``. Opt-in OFF by default: anything other than an
explicit truthy value — and the CM-18 ``REPLACE-ME`` Key Vault seed placeholder
— is treated as unset. Mirrors the existing flag conventions
(``observability.langfuse_export._env_or_none`` and the portal API's
``isTenantAdminEnabled``).
"""

from __future__ import annotations

import os

# Same truthy vocabulary as the portal API's TENANT_ADMIN_ENABLED gate.
_ENABLED_VALUES = frozenset({"1", "true", "yes", "on"})


def is_webchat_enabled() -> bool:
    """True only when ``WEBCHAT_TEST_ENABLED`` is an explicit truthy value."""
    raw = os.environ.get("WEBCHAT_TEST_ENABLED", "").strip().lower()
    if not raw or raw == "replace-me":  # empty / KV placeholder => unset
        return False
    return raw in _ENABLED_VALUES
