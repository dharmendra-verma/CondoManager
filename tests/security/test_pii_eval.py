"""Offline PII-detection eval (CM-38 AC1).

Runs the deterministic ``RegexPiiDetector`` over a golden seed file and gates
recall at 100% for the categories the regex detector covers (email / phone /
credit_card / api_key). Mirrors the CM-30 triage eval + CM-36 recurring eval
pattern: a checked-in JSONL of labelled examples + an exact-accuracy gate, so a
regression in the detection regexes fails CI loudly.

PERSON / ADDRESS / GOV_ID are intentionally absent from the seed — the regex
detector does not (and must not) claim them; that coverage is the Azure AI
Language detector's job and is exercised separately via the seam, not here.
"""

from __future__ import annotations

import json
from pathlib import Path

from agents.security.detection import RegexPiiDetector
from agents.security.models import PiiCategory

SEED = Path(__file__).resolve().parents[1] / "eval" / "security_pii_seed.jsonl"

# Categories the offline detector is responsible for.
COVERED = {
    PiiCategory.EMAIL,
    PiiCategory.PHONE,
    PiiCategory.CREDIT_CARD,
    PiiCategory.API_KEY,
}


def _load() -> list[dict[str, object]]:
    rows = [
        json.loads(line)
        for line in SEED.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) >= 10, "seed should carry a meaningful number of examples"
    return rows


def test_seed_recall_is_perfect_on_covered_categories() -> None:
    detector = RegexPiiDetector()
    misses: list[str] = []
    false_positives: list[str] = []

    for row in _load():
        text = str(row["text"])
        expected = {PiiCategory(c) for c in row["categories"]}  # type: ignore[union-attr]
        found = {e.category for e in detector.detect(text)}

        # Recall: every expected covered category must be found.
        if not expected.issubset(found):
            misses.append(f"{text!r}: expected {expected}, found {found}")
        # Precision guard: the detector must not invent a covered category that
        # the label set doesn't contain (clean lines must stay clean).
        spurious = (found & COVERED) - expected
        if spurious:
            false_positives.append(f"{text!r}: spurious {spurious}")

    assert not misses, "recall failures:\n" + "\n".join(misses)
    assert not false_positives, "precision failures:\n" + "\n".join(false_positives)
