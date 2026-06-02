"""Structured PII detection seam (CM-38 AC1).

Two implementations behind one ``PiiDetector`` Protocol, selected by env var
exactly like ``get_ticket_repository`` / ``get_checkpointer``:

* :class:`RegexPiiDetector` — the **offline default**. Reuses the same
  conservative shapes CM-27 masks (email / E.164 phone / Luhn-valid card /
  known API-key prefixes) and returns them as structured :class:`PiiEntity`
  spans. Deterministic, zero deps, zero network — this is what CI and local
  dev run.
* :class:`AzureLanguagePiiDetector` — the rich path. Calls Azure AI Language's
  PII recognition (``recognize_pii_entities``) which additionally classifies
  PERSON / ADDRESS / government IDs. ``azure.ai.textanalytics`` is lazy-imported
  and only reached when ``AI_LANGUAGE_ENDPOINT`` is set, so it never loads in
  CI. Install it with the optional ``security`` extra.

``get_pii_detector()`` returns the Azure detector when ``AI_LANGUAGE_ENDPOINT``
is a real value, else the regex detector. Cached singleton + ``_reset_for_tests``.

Detection (finding + classifying spans) is deliberately separate from masking:
masking is owned by CM-27 ``mask_text`` (see ``masking.py``). Detection answers
"what PII is in here and where" for the audit/redaction paths; masking answers
"make this safe to log".
"""

from __future__ import annotations

import logging
import os
import re
from typing import Protocol, runtime_checkable

from .models import PiiCategory, PiiEntity

_log = logging.getLogger(__name__)

SECRET_PLACEHOLDER = "REPLACE-ME"

# Patterns mirror agents/observability/pii.py (CM-27). Kept local rather than
# importing the private regexes there so detection can attach a category to
# each shape; the two are intentionally the same set so log masking and
# detection agree on what counts as structured PII.
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_E164 = re.compile(r"\+\d{8,15}\b")
_CARD_LIKE = re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{1,7}\b")
_API_KEY = re.compile(
    r"\b(?:"
    r"sk-[A-Za-z0-9_-]{16,}"
    r"|pk_[a-z]+_[A-Za-z0-9]{16,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|ASIA[0-9A-Z]{16}"
    r")\b"
)


def _luhn_ok(digits: str) -> bool:
    """Standard Luhn check — gates the credit-card category (mirrors CM-27)."""
    total = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@runtime_checkable
class PiiDetector(Protocol):
    """Finds + classifies PII spans in free text."""

    def detect(self, text: str) -> list[PiiEntity]:
        """Return the PII entities found in ``text`` (possibly empty)."""
        ...


class RegexPiiDetector:
    """Offline, deterministic detector over the CM-27 structured shapes.

    Emits EMAIL / PHONE / CREDIT_CARD / API_KEY only. PERSON / ADDRESS /
    GOV_ID are *not* detectable by regex with acceptable precision, so this
    detector never claims them — that coverage requires the Azure detector.
    Confidence is always ``1.0`` (a regex either matches or it does not).
    """

    # Order is the masking order from CM-27: API keys before email (a
    # fabricated ``sk-...@...`` is a key, not an email), phone before card
    # (``+919876543210`` would otherwise match the card shape).
    def detect(self, text: str) -> list[PiiEntity]:
        spans: list[PiiEntity] = []
        claimed: list[tuple[int, int]] = []

        def _overlaps(start: int, end: int) -> bool:
            return any(start < e and s < end for s, e in claimed)

        def _collect(pattern: re.Pattern[str], category: PiiCategory) -> None:
            for m in pattern.finditer(text):
                start, end = m.start(), m.end()
                if _overlaps(start, end):
                    continue
                if category is PiiCategory.CREDIT_CARD:
                    digits = re.sub(r"\D", "", m.group(0))
                    if not (13 <= len(digits) <= 19 and _luhn_ok(digits)):
                        continue  # likely a phone / order id, not a card
                claimed.append((start, end))
                spans.append(
                    PiiEntity(
                        category=category,
                        text=m.group(0),
                        start=start,
                        end=end,
                        confidence=1.0,
                    )
                )

        _collect(_API_KEY, PiiCategory.API_KEY)
        _collect(_EMAIL, PiiCategory.EMAIL)
        _collect(_PHONE_E164, PiiCategory.PHONE)
        _collect(_CARD_LIKE, PiiCategory.CREDIT_CARD)

        spans.sort(key=lambda e: e.start)
        return spans


# Azure AI Language category string -> our PiiCategory. The service uses
# coarse category names; anything we don't map (e.g. "DateTime", "URL") is
# intentionally dropped — we only audit/redact the categories the AC names.
_AZURE_CATEGORY_MAP: dict[str, PiiCategory] = {
    "Person": PiiCategory.PERSON,
    "PersonType": PiiCategory.PERSON,
    "Address": PiiCategory.ADDRESS,
    "PhoneNumber": PiiCategory.PHONE,
    "Email": PiiCategory.EMAIL,
    "CreditCardNumber": PiiCategory.CREDIT_CARD,
    "USSocialSecurityNumber": PiiCategory.GOV_ID,
    "Organization": PiiCategory.PERSON,  # org names can carry a tenant's identity
}


class AzureLanguagePiiDetector:
    """Azure AI Language PII recognition — the rich, name/address-aware path.

    ``azure.ai.textanalytics`` + ``DefaultAzureCredential`` are lazy-imported
    so processes on the regex path pay nothing. Auth routes through the CM-18
    User-Assigned MI in prod (``az login`` locally). A service failure degrades
    to "no entities" + a warning rather than raising into the caller — a PII
    *detection* outage must not 500 a tenant message (masking via ``mask_text``
    still runs on the regex path regardless).
    """

    def __init__(self, *, endpoint: str) -> None:
        from azure.ai.textanalytics import TextAnalyticsClient  # noqa: PLC0415
        from azure.identity import DefaultAzureCredential  # noqa: PLC0415

        credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        self._client = TextAnalyticsClient(endpoint=endpoint, credential=credential)

    def detect(self, text: str) -> list[PiiEntity]:
        if not text:
            return []
        try:
            results = self._client.recognize_pii_entities([text])
        except Exception as e:  # noqa: BLE001 — detection outage must not raise
            _log.warning("Azure PII detection failed; returning no entities: %s", e)
            return []
        spans: list[PiiEntity] = []
        for doc in results:
            if getattr(doc, "is_error", False):
                continue
            for ent in doc.entities:
                category = _AZURE_CATEGORY_MAP.get(ent.category)
                if category is None:
                    continue
                spans.append(
                    PiiEntity(
                        category=category,
                        text=ent.text,
                        start=ent.offset,
                        end=ent.offset + ent.length,
                        confidence=float(ent.confidence_score),
                    )
                )
        spans.sort(key=lambda e: e.start)
        return spans


_cached: PiiDetector | None = None


def get_pii_detector() -> PiiDetector:
    """Return the configured detector (cached). Offline default: regex.

    ``AI_LANGUAGE_ENDPOINT`` unset / blank / ``REPLACE-ME`` -> regex detector;
    otherwise the Azure AI Language detector.
    """
    global _cached
    if _cached is not None:
        return _cached
    endpoint = os.environ.get("AI_LANGUAGE_ENDPOINT", "").strip()
    if not endpoint or endpoint == SECRET_PLACEHOLDER:
        _log.info("AI_LANGUAGE_ENDPOINT not set; using RegexPiiDetector (offline)")
        _cached = RegexPiiDetector()
    else:
        _cached = AzureLanguagePiiDetector(endpoint=endpoint)
    return _cached


def _reset_for_tests() -> None:
    """Drop the cached detector so each test starts clean."""
    global _cached
    _cached = None
