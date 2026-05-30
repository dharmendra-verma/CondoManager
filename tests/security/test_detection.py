"""PII detection tests (CM-38 AC1)."""

from __future__ import annotations

import pytest
from agents.security.detection import (
    RegexPiiDetector,
    get_pii_detector,
)
from agents.security.models import PiiCategory


def test_detects_email() -> None:
    spans = RegexPiiDetector().detect("ping me at jane.doe@example.com please")
    assert len(spans) == 1
    e = spans[0]
    assert e.category is PiiCategory.EMAIL
    assert e.text == "jane.doe@example.com"
    # Offsets are exact: text[start:end] is the entity.
    assert "ping me at jane.doe@example.com please"[e.start : e.end] == e.text
    assert e.confidence == 1.0


def test_detects_e164_phone() -> None:
    spans = RegexPiiDetector().detect("call +14155552671 now")
    assert [s.category for s in spans] == [PiiCategory.PHONE]
    assert spans[0].text == "+14155552671"


def test_detects_luhn_valid_card_only() -> None:
    # 4111111111111111 passes Luhn; the 13-digit run below does not.
    valid = RegexPiiDetector().detect("card 4111 1111 1111 1111")
    assert [s.category for s in valid] == [PiiCategory.CREDIT_CARD]

    invalid = RegexPiiDetector().detect("order 1234 5678 9012 3456")
    assert invalid == []  # fails Luhn -> not flagged as a card


def test_detects_api_key() -> None:
    spans = RegexPiiDetector().detect("token sk-ABCDEFGHIJKLMNOP1234 leaked")
    assert [s.category for s in spans] == [PiiCategory.API_KEY]


def test_phone_not_misclassified_as_card() -> None:
    # +919876543210 is 12 digits — would match the card shape, but the phone
    # pattern claims it first (CM-27 ordering), so it's PHONE not CREDIT_CARD.
    spans = RegexPiiDetector().detect("+919876543210")
    assert [s.category for s in spans] == [PiiCategory.PHONE]


def test_multiple_entities_sorted_by_offset() -> None:
    text = "mail a@b.co or call +14155552671"
    spans = RegexPiiDetector().detect(text)
    cats = [s.category for s in spans]
    assert cats == [PiiCategory.EMAIL, PiiCategory.PHONE]
    assert spans[0].start < spans[1].start


@pytest.mark.parametrize("text", ["", "   ", "no pii here at all"])
def test_clean_text_yields_nothing(text: str) -> None:
    assert RegexPiiDetector().detect(text) == []


def test_regex_detector_never_emits_person_or_address() -> None:
    # The regex detector cannot classify names/addresses — that's the Azure
    # detector's job. Guard the documented limitation so a future regex tweak
    # can't silently start emitting low-precision PERSON spans.
    spans = RegexPiiDetector().detect("John Smith lives at 123 Main Street")
    assert all(s.category not in (PiiCategory.PERSON, PiiCategory.ADDRESS) for s in spans)


def test_get_pii_detector_offline_default_is_regex() -> None:
    # conftest clears AI_LANGUAGE_ENDPOINT, so the offline detector is selected.
    assert isinstance(get_pii_detector(), RegexPiiDetector)


def test_get_pii_detector_treats_placeholder_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_LANGUAGE_ENDPOINT", "REPLACE-ME")
    assert isinstance(get_pii_detector(), RegexPiiDetector)
