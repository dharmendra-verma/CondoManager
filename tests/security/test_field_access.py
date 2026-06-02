"""Field-level access control tests (CM-38 AC3)."""

from __future__ import annotations

from agents.security.field_access import (
    REDACTED,
    is_pii_field,
    redact_document,
)
from agents.security.models import AccessRole

DOC = {
    "id": "TKT-1",
    "unit": "4b",
    "category": "plumbing",
    "contact_email": "jane@example.com",
    "phone": "+14155552671",
    "issue_text": "sink leaking, call me",
    "status": "New",
}


def test_is_pii_field_case_insensitive() -> None:
    assert is_pii_field("contact_email")
    assert is_pii_field("Phone")
    assert not is_pii_field("category")


def test_manager_sees_contact_pii() -> None:
    out = redact_document(DOC, AccessRole.MANAGER)
    assert out["contact_email"] == "jane@example.com"
    assert out["phone"] == "+14155552671"
    assert out["issue_text"] == "sink leaking, call me"
    # Non-PII always passes through.
    assert out["category"] == "plumbing"


def test_analytics_role_sees_no_pii() -> None:
    out = redact_document(DOC, AccessRole.ANALYTICS)
    assert out["contact_email"] == REDACTED
    assert out["phone"] == REDACTED
    assert out["issue_text"] == REDACTED
    # Non-PII still visible — analytics needs category/status to aggregate.
    assert out["category"] == "plumbing"
    assert out["status"] == "New"


def test_agent_sees_issue_text_but_not_contact() -> None:
    out = redact_document(DOC, AccessRole.AGENT)
    assert out["issue_text"] == "sink leaking, call me"  # per-field override
    assert out["contact_email"] == REDACTED
    assert out["phone"] == REDACTED


def test_unknown_role_fails_closed() -> None:
    out = redact_document(DOC, "intruder")
    assert out["contact_email"] == REDACTED
    assert out["phone"] == REDACTED
    assert out["issue_text"] == REDACTED


def test_none_role_fails_closed() -> None:
    out = redact_document(DOC, None)
    assert out["contact_email"] == REDACTED


def test_nested_and_list_documents_redacted() -> None:
    nested = {
        "ticket": "TKT-2",
        "tenant": {"name": "Jane", "email": "jane@example.com"},
        "notes": [
            {"summary": "called tenant at jane@example.com"},
            {"category": "hvac"},
        ],
    }
    out = redact_document(nested, AccessRole.ANALYTICS)
    assert out["tenant"]["name"] == REDACTED
    assert out["tenant"]["email"] == REDACTED
    assert out["notes"][0]["summary"] == REDACTED
    assert out["notes"][1]["category"] == "hvac"


def test_redaction_does_not_mutate_input() -> None:
    original = dict(DOC)
    redact_document(DOC, AccessRole.ANALYTICS)
    assert DOC == original  # input untouched; a copy is returned
