"""``agents.security`` — PII detection, masking, field-level access, and audit.

Jira: CM-38  | Epic: CM-Epic 13 (Security & Compliance)  | Phase 1

This package implements the compliance controls a SOC2 audit expects on day
one. It **builds on** the CM-27 starter masker (``agents.observability.pii``)
rather than replacing it — ``mask_text`` is the one masking entry point shared
by the log filter (CM-27) and the new trace-layer processor here.

Public surface (re-exported for convenience; also importable from submodules):

* ``mask_text(text)`` — the single masking facade (delegates to CM-27).
* ``get_pii_detector()`` — env-gated structured PII detector (regex offline,
  Azure AI Language when ``AI_LANGUAGE_ENDPOINT`` is set). **AC1**
* ``PiiMaskingSpanProcessor`` — OTel span processor masking string attributes.
  **AC2** (the log layer is already covered by CM-27's ``PiiMaskingFilter``).
* ``redact_document(doc, role)`` — application-layer field-level access. **AC3**
* ``get_audit_sink()`` / ``record_audit(...)`` — append-only audit log. **AC4**
* ``RETENTION_POLICY`` / ``delete_tenant_data(...)`` — retention + right-to-
  erasure. **AC6**

The SOC2 CC1–CC9 control matrix (**AC5**) lives in ``docs/SECURITY.md``.

Design principles match the rest of the agent packages: env-gated seams
(cached singleton + ``_reset_for_tests``), lazy Azure SDK imports, and pure
deterministic logic so the whole package is offline-testable with no Azure
calls in CI.
"""

from __future__ import annotations

from .audit import (
    AuditSink,
    CosmosAuditSink,
    InMemoryAuditSink,
    get_audit_sink,
    record_audit,
)
from .detection import (
    AzureLanguagePiiDetector,
    PiiDetector,
    RegexPiiDetector,
    get_pii_detector,
)
from .field_access import PII_FIELDS, is_pii_field, redact_document
from .masking import PiiMaskingSpanProcessor, mask_text
from .models import (
    AccessRole,
    AuditAction,
    AuditEvent,
    PiiCategory,
    PiiEntity,
)
from .retention import (
    RETENTION_POLICY,
    DeletionReport,
    ErasableSource,
    delete_tenant_data,
)

__all__ = [
    "PII_FIELDS",
    "RETENTION_POLICY",
    "AccessRole",
    "AuditAction",
    "AuditEvent",
    "AuditSink",
    "AzureLanguagePiiDetector",
    "CosmosAuditSink",
    "DeletionReport",
    "ErasableSource",
    "InMemoryAuditSink",
    "PiiCategory",
    "PiiDetector",
    "PiiEntity",
    "PiiMaskingSpanProcessor",
    "RegexPiiDetector",
    "delete_tenant_data",
    "get_audit_sink",
    "get_pii_detector",
    "is_pii_field",
    "mask_text",
    "record_audit",
    "redact_document",
]
