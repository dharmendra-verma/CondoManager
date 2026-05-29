"""Tests for ``agents.observability.pii.mask_pii``.

Each pattern in the starter set is exercised independently, plus the
order-of-operations invariants, plus pass-through cases that should NOT
be touched (UUIDs, plain prose, non-Luhn card-shaped strings).
"""

from __future__ import annotations

from agents.observability.pii import mask_pii


# -----------------------------------------------------------------------
# Each pattern, in isolation
# -----------------------------------------------------------------------


def test_email_masked() -> None:
    assert mask_pii("contact: to.dh@example.com") == "contact: ***@***.***"


def test_email_with_plus_addressing_masked() -> None:
    assert mask_pii("send to user+filter@sub.example.co") == "send to ***@***.***"


def test_phone_e164_masked() -> None:
    assert mask_pii("from +919876543210") == "from +***"


def test_phone_short_e164_masked() -> None:
    """E.164 minimum is 8 digits — country code + subscriber."""
    assert mask_pii("call +14155552671 now") == "call +*** now"


def test_card_luhn_pass_masked() -> None:
    # Standard test card from Visa (4111-1111-1111-1111) — passes Luhn.
    assert mask_pii("paid with 4111-1111-1111-1111") == "paid with ****-****-****-1111"


def test_card_with_spaces_luhn_pass_masked() -> None:
    assert mask_pii("paid 4111 1111 1111 1111") == "paid ****-****-****-1111"


def test_card_non_luhn_passes_through() -> None:
    """Card-shaped but invalid Luhn — probably an order id or phone fragment."""
    raw = "order id 1234-5678-9012-3456"
    assert mask_pii(raw) == raw


def test_api_key_sk_masked() -> None:
    assert mask_pii("OPENAI_API_KEY=sk-abcdef1234567890ABCDEF") == (
        "OPENAI_API_KEY=***REDACTED-KEY***"
    )


def test_api_key_pk_masked() -> None:
    assert mask_pii("LANGFUSE_PUBLIC_KEY=pk_lf_abcdefghij1234567890") == (
        "LANGFUSE_PUBLIC_KEY=***REDACTED-KEY***"
    )


def test_api_key_aws_akia_masked() -> None:
    assert mask_pii("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE") == (
        "AWS_ACCESS_KEY_ID=***REDACTED-KEY***"
    )


def test_api_key_aws_asia_masked() -> None:
    assert mask_pii("AWS_SESSION_TOKEN=ASIAIOSFODNN7EXAMPLE") == (
        "AWS_SESSION_TOKEN=***REDACTED-KEY***"
    )


# -----------------------------------------------------------------------
# Order-of-operations + multi-pattern input
# -----------------------------------------------------------------------


def test_mixed_email_and_phone_in_one_pass() -> None:
    inp = "contact to.dh@example.com or +919876543210 for help"
    out = mask_pii(inp)
    assert "to.dh@example.com" not in out
    assert "+919876543210" not in out
    assert "***@***.***" in out
    assert "+***" in out


def test_api_key_processed_before_email() -> None:
    """Order safety: an API-key-shaped string that ALSO looks email-ish
    should not be processed by email first and corrupted."""
    inp = "key sk-abcdef1234567890ABCDEF"
    assert "***REDACTED-KEY***" in mask_pii(inp)


# -----------------------------------------------------------------------
# Pass-through — NOT touched
# -----------------------------------------------------------------------


def test_uuid_passes_through() -> None:
    """UUID-shaped strings must NOT match the card / phone patterns."""
    uuid_str = "65e0b16a-b7cf-5047-9255-ce5b79391c1b"
    assert mask_pii(f"id={uuid_str}") == f"id={uuid_str}"


def test_request_id_passes_through() -> None:
    """The CM-21 request_id format ``req_<12hex>`` must not be touched."""
    rid = "req_a1b2c3d4e5f6"
    assert mask_pii(f"event request_id={rid}") == f"event request_id={rid}"


def test_plain_prose_passes_through() -> None:
    inp = "The maintenance worker arrived on time."
    assert mask_pii(inp) == inp


def test_empty_string_passes_through() -> None:
    assert mask_pii("") == ""


def test_short_digit_run_not_matched_as_card() -> None:
    """A 4-digit number alone is not card-shaped (need 13–19 digits total)."""
    assert mask_pii("count is 1234") == "count is 1234"
