"""Starter-set PII masking for log lines.

Jira: CM-27  | Epic: Observability  | Phase 0

Single function ``mask_pii(text)`` runs ~four conservative regexes over
free-text and replaces matches with redaction tokens. Designed for the
``logging.Filter`` in ``agents.observability.logging``, but the function
is pure so it can be reused for other sinks (Langfuse `input`/`output`
fields, App Insights message bodies if we ever route logs there).

**Not exhaustive.** This is a starter set covering the categories the
foundation phase actually handles:

  * Email addresses
  * Phone numbers in E.164 format (Twilio WhatsApp default)
  * Credit-card-shaped digit runs that pass the Luhn check
  * API key prefixes used in this project (sk-..., pk_..., AKIA..., ASIA...)

Names, addresses, free-text PII, locale-specific phone formats — all out
of scope. The comprehensive coverage is its own (much larger) story.

Design principles:

  * **Conservative regexes.** False-positives at log layer are worse than
    false-negatives (a redacted UUID hurts more than a leaked borderline
    string). We pick patterns that match obvious PII shapes only.
  * **Format-preserving where useful.** Email becomes ``***@***.***``,
    credit cards become ``****-****-****-1234`` — keeps log diffs
    readable while removing the sensitive payload.
  * **No external deps.** Stdlib ``re`` only; this file is safe to import
    from any module without lazy-loading.
"""

from __future__ import annotations

import re

# Conservative regex set. False-positives are worse than false-negatives at
# log layer — we'd rather see a borderline email than mangle a UUID.

# Standard RFC-ish email shape. Anchored on word boundaries to avoid
# matching inside e.g. `user@host` shell-style strings inside URLs.
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_EMAIL_REPL = "***@***.***"

# E.164 phone number: '+' followed by 8–15 digits. Twilio WhatsApp tenant IDs
# and incoming sender numbers fit this exactly. Non-E.164 formats
# ((555) 123-4567, +1 555-...) are NOT matched — documented limitation.
_PHONE_E164 = re.compile(r"\+\d{8,15}\b")
_PHONE_REPL = "+***"

# Credit-card-shaped: 13–19 digits in groups of 4 with optional - or space
# separators. Mask only if the Luhn check passes; else pass through (probably
# a phone number or order id that happens to be the right shape).
_CARD_LIKE = re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{1,7}\b")

# API key prefixes we actually emit / consume:
#   sk-...   OpenAI / Anthropic / generic secret keys (16+ alnum chars)
#   pk_...   Langfuse public keys (e.g. pk_lf_<32 chars>)
#   AKIA...  AWS access key IDs (16 chars after prefix)
#   ASIA...  AWS STS session keys (16 chars after prefix)
# Word-boundary-anchored so prose like "the AKIA prefix" doesn't trigger.
_API_KEY = re.compile(
    r"\b(?:"
    r"sk-[A-Za-z0-9_-]{16,}"
    r"|pk_[a-z]+_[A-Za-z0-9]{16,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|ASIA[0-9A-Z]{16}"
    r")\b"
)
_API_KEY_REPL = "***REDACTED-KEY***"


def _luhn_ok(digits: str) -> bool:
    """Standard Luhn check on a string of digits."""
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


def _mask_card(match: "re.Match[str]") -> str:
    raw = match.group(0)
    digits = re.sub(r"\D", "", raw)
    if 13 <= len(digits) <= 19 and _luhn_ok(digits):
        return "****-****-****-" + digits[-4:]
    return raw  # not a real card — likely a phone or order id


def mask_pii(text: str) -> str:
    """Apply the starter-set patterns to ``text`` in one pass.

    Order matters:
      1. API keys first — a fabricated ``sk-pretend@example.com`` should
         hit the API-key path, not the email path.
      2. Email next — most common pattern, anchored shape.
      3. E.164 phone before card — '+919876543210' would otherwise match
         the 13-digit card shape.
      4. Credit card last — Luhn check gates the mask.
    """
    text = _API_KEY.sub(_API_KEY_REPL, text)
    text = _EMAIL.sub(_EMAIL_REPL, text)
    text = _PHONE_E164.sub(_PHONE_REPL, text)
    text = _CARD_LIKE.sub(_mask_card, text)
    return text
