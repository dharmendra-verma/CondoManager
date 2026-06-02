"""Pydantic models + enums for the security/compliance surface (CM-38).

Kept dependency-free (Pydantic v2 + stdlib enum only) so every other module
in the package — and the tests — can import these without pulling in Azure
SDKs or OpenTelemetry.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class PiiCategory(StrEnum):
    """Categories the detector classifies (AC1).

    The regex detector covers the structured shapes (EMAIL/PHONE/CREDIT_CARD/
    API_KEY); PERSON/ADDRESS/GOV_ID are recognised only by the Azure AI
    Language detector — the regex detector never emits them (documented in
    ``detection.py``) so we don't pretend coverage we don't have.
    """

    PERSON = "person"
    ADDRESS = "address"
    PHONE = "phone"
    EMAIL = "email"
    GOV_ID = "gov_id"
    CREDIT_CARD = "credit_card"
    API_KEY = "api_key"


class PiiEntity(BaseModel):
    """One detected PII span within a piece of text.

    ``start``/``end`` are character offsets (``text[start:end] == text_slice``)
    so a caller can redact precisely. ``confidence`` is in ``[0, 1]``; the
    regex detector reports ``1.0`` (a pattern either matches or it doesn't),
    the Azure detector passes through its model score.
    """

    category: PiiCategory
    text: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)


class AccessRole(StrEnum):
    """Who is reading a document — drives field-level redaction (AC3).

    Ordered loosely from most to least privileged for PII visibility. An
    unknown / unmapped role is treated as the most restrictive (fail-closed)
    by ``field_access.redact_document``.
    """

    AUDITOR = "auditor"      # compliance/audit — sees everything, read-only
    MANAGER = "manager"      # building manager — sees tenant contact info
    AGENT = "agent"          # an automated agent acting in-flow
    ANALYTICS = "analytics"  # aggregate reporting — no raw PII
    TENANT = "tenant"        # a tenant viewing their own portal data


class AuditAction(StrEnum):
    """The verb recorded on an audit event (AC4)."""

    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    ACCESS = "access"   # generic data access (e.g. a query)
    EXPORT = "export"   # data leaving the system (digest, report)
    ERASE = "erase"     # right-to-erasure deletion


class AuditEvent(BaseModel):
    """An immutable audit-log record (AC4).

    ``frozen=True`` makes instances hashable and prevents in-place mutation —
    the in-code half of the "immutable retention" requirement (the storage
    half is the append-only sink + ``defaultTtl: -1`` audit container +
    continuous backup, documented in ``docs/SECURITY.md``).

    ``detail`` is free text and is **masked** (CM-27 ``mask_text``) by the
    ``audit()`` helper before the event is constructed, so a tenant's
    phone/email never lands in the audit trail in plaintext.
    """

    model_config = {"frozen": True}

    id: str
    ts: datetime
    action: AuditAction
    actor: str
    resource: str
    tenant_id: str | None = None
    detail: str = ""

    def to_doc(self) -> dict[str, object]:
        """Cosmos document form — adds the ``/tenantId`` partition-key path.

        ``tenant_id`` may be ``None`` (system actions not scoped to a tenant);
        such events partition under the literal ``"_system"`` so they remain
        queryable rather than landing in a null partition.
        """
        doc = self.model_dump(mode="json")
        doc["tenantId"] = self.tenant_id or "_system"
        return doc
