"""Application-layer field-level access control (CM-38 AC3).

**Why application-layer.** Azure Cosmos DB has no native field/column-level
RBAC — its data-plane RBAC (provisioned in ``infra/bicep/modules/cosmos-rbac.bicep``)
grants access at the *account/database/container* granularity only. Field-level
restriction is therefore enforced here, in the repository read path, and the
two halves are documented together in ``docs/SECURITY.md`` so the split is
explicit and not security theatre.

The model: a fixed set of field names are classified as PII (:data:`PII_FIELDS`),
and :data:`FIELD_VISIBILITY` declares which :class:`AccessRole` values may see
each one. :func:`redact_document` returns a projected copy with every PII field
the role may not see replaced by :data:`REDACTED`. Non-PII fields always pass
through. An unknown / unmapped role is treated as the most restrictive
(fail-closed): it sees no PII.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models import AccessRole

REDACTED = "***REDACTED***"

# Field names (snake_case domain + a couple of common doc aliases) that carry
# tenant PII. Matching is case-insensitive on the exact field name.
PII_FIELDS: frozenset[str] = frozenset(
    {
        "full_name",
        "name",
        "tenant_name",
        "contact_email",
        "email",
        "phone",
        "phone_number",
        "address",
        "unit_address",
        "gov_id",
        "ssn",
        "issue_text",  # free-text tenant message — may embed any of the above
        "summary",     # composed free text shown to managers
    }
)

# Which roles are permitted to see PII fields. Roles not listed see no PII.
# AUDITOR + MANAGER need contact info to do their jobs; ANALYTICS works on
# aggregates and must never receive raw PII; TENANT sees their *own* data via a
# separate tenant-scoped path, so the generic projection treats them as
# PII-restricted here (the portal passes the tenant's own already-scoped doc).
_PII_VISIBLE_ROLES: frozenset[AccessRole] = frozenset(
    {AccessRole.AUDITOR, AccessRole.MANAGER}
)

# Per-field overrides, when a single field needs a different rule than the
# blanket one. The AGENT role may read the free-text request fields (it must, to
# act on the request) but no other PII.
FIELD_VISIBILITY: dict[str, frozenset[AccessRole]] = {
    "issue_text": _PII_VISIBLE_ROLES | {AccessRole.AGENT},
    "summary": _PII_VISIBLE_ROLES | {AccessRole.AGENT},
}


def is_pii_field(name: str) -> bool:
    """True if ``name`` is classified as a PII field (case-insensitive)."""
    return name.lower() in PII_FIELDS


def _may_see(field: str, role: AccessRole | None) -> bool:
    """Whether ``role`` may see the value of PII ``field``. ``None`` -> never."""
    if role is None:
        return False
    allowed = FIELD_VISIBILITY.get(field.lower(), _PII_VISIBLE_ROLES)
    return role in allowed


def _resolve(role: AccessRole | str) -> AccessRole | None:
    """Coerce a role to :class:`AccessRole`; unknown strings -> ``None`` (closed)."""
    if isinstance(role, AccessRole):
        return role
    try:
        return AccessRole(role)
    except ValueError:
        return None


def redact_document(doc: Mapping[str, Any], role: AccessRole | str | None) -> dict[str, Any]:
    """Return a copy of ``doc`` with PII fields the ``role`` may not see redacted.

    Non-PII fields are copied through unchanged. PII fields are replaced with
    :data:`REDACTED` unless the role is permitted to see that field. An unknown
    role string (one not in :class:`AccessRole`) — or ``None`` — fails closed:
    no PII is visible.

    Nested mappings are redacted recursively; lists of mappings are redacted
    element-wise. Other value types pass through as-is.
    """
    resolved = None if role is None else _resolve(role)
    out: dict[str, Any] = {}
    for key, value in doc.items():
        if isinstance(value, Mapping):
            out[key] = redact_document(value, resolved)
        elif isinstance(value, list):
            out[key] = [
                redact_document(item, resolved) if isinstance(item, Mapping) else item
                for item in value
            ]
        elif is_pii_field(key) and not _may_see(key, resolved):
            out[key] = REDACTED
        else:
            out[key] = value
    return out
