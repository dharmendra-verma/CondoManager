"""Offline eval tests (CM-30 AC #7) — dataset well-formedness + scorer math.

No OpenAI key required. The >90% accuracy gate against the real model lives
in ``infra/scripts/eval-triage.py`` (operator-run); here we prove the dataset
is large, valid, and balanced, and that the scoring functions are correct.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from agents.orchestrator.eval import (
    accuracy,
    run_eval,
    score_classification,
)
from agents.orchestrator.state import Intent, Tone, Urgency
from agents.orchestrator.triage import TriageClassification

_SEED = Path(__file__).parent / "triage_seed.jsonl"

_VALID_INTENTS = {e.value for e in Intent if e is not Intent.UNKNOWN}
_VALID_URGENCIES = {e.value for e in Urgency}
_VALID_TONES = {e.value for e in Tone}


def _load() -> list[dict]:
    rows: list[dict] = []
    with _SEED.open(encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if line:
                rows.append(json.loads(line))
    return rows


# --- Dataset well-formedness -------------------------------------------------


def test_dataset_has_at_least_200_examples() -> None:
    assert len(_load()) >= 200


def test_every_line_is_valid_json_with_required_shape() -> None:
    line_num = 0
    with _SEED.open(encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            line_num += 1
            ex = json.loads(line)  # raises on malformed JSON -> test fails loudly
            assert ex["inputs"]["message"], f"line {line_num}: empty message"
            assert ex["inputs"]["tenant_id"], f"line {line_num}: empty tenant_id"
            out = ex["outputs"]
            assert out["intent"] in _VALID_INTENTS, f"line {line_num}: bad intent"
            assert out["urgency"] in _VALID_URGENCIES, f"line {line_num}: bad urgency"
            assert out["tone"] in _VALID_TONES, f"line {line_num}: bad tone"


def test_dataset_covers_every_label_value() -> None:
    rows = _load()
    intents = {r["outputs"]["intent"] for r in rows}
    urgencies = {r["outputs"]["urgency"] for r in rows}
    tones = {r["outputs"]["tone"] for r in rows}
    assert intents == _VALID_INTENTS
    assert urgencies == _VALID_URGENCIES
    assert tones == _VALID_TONES


def test_dataset_is_reasonably_balanced_by_intent() -> None:
    """No single intent should swamp the set (each at least 10%)."""
    rows = _load()
    counts = Counter(r["outputs"]["intent"] for r in rows)
    n = len(rows)
    for intent in _VALID_INTENTS:
        assert counts[intent] >= 0.10 * n, (
            f"intent {intent!r} under-represented: {counts[intent]}/{n}"
        )


def test_tenant_ids_are_unique() -> None:
    rows = _load()
    ids = [r["inputs"]["tenant_id"] for r in rows]
    assert len(ids) == len(set(ids))


# --- Scorer math -------------------------------------------------------------


def test_score_classification_all_correct() -> None:
    pred = TriageClassification(
        intent=Intent.MAINTENANCE, urgency=Urgency.HIGH, tone=Tone.FRUSTRATED
    )
    ref = {"intent": "maintenance", "urgency": "high", "tone": "frustrated"}
    assert score_classification(pred, ref) == {"intent": True, "urgency": True, "tone": True}


def test_score_classification_partial() -> None:
    pred = TriageClassification(
        intent=Intent.MAINTENANCE, urgency=Urgency.LOW, tone=Tone.NEUTRAL
    )
    ref = {"intent": "maintenance", "urgency": "high", "tone": "neutral"}
    assert score_classification(pred, ref) == {"intent": True, "urgency": False, "tone": True}


def test_score_only_compares_present_reference_fields() -> None:
    pred = TriageClassification(
        intent=Intent.INQUIRY, urgency=Urgency.LOW, tone=Tone.NEUTRAL
    )
    assert score_classification(pred, {"intent": "inquiry"}) == {"intent": True}


class _ScriptedClassifier:
    """Returns a fixed classification regardless of input — for scorer tests."""

    cost_per_call_usd = 0.0

    def __init__(self, result: TriageClassification) -> None:
        self._result = result

    def classify(self, message: str, history: list[dict]) -> TriageClassification:
        return self._result


def test_run_eval_computes_intent_accuracy() -> None:
    # Classifier always says maintenance/medium/neutral.
    clf = _ScriptedClassifier(
        TriageClassification(
            intent=Intent.MAINTENANCE, urgency=Urgency.MEDIUM, tone=Tone.NEUTRAL
        )
    )
    examples = [
        {"inputs": {"message": "a", "tenant_id": "t1"},
         "outputs": {"intent": "maintenance", "urgency": "medium", "tone": "neutral"}},
        {"inputs": {"message": "b", "tenant_id": "t2"},
         "outputs": {"intent": "inquiry", "urgency": "low", "tone": "neutral"}},
    ]
    report = run_eval(clf, examples)
    assert report.n == 2
    assert report.intent_accuracy == 0.5  # 1 of 2 intents correct
    assert report.accuracy["tone"] == 1.0  # both neutral
    assert not report.passed  # 0.5 does not clear the >0.90 gate
    assert len(report.mismatches("intent")) == 1


def test_accuracy_empty_is_zero() -> None:
    assert accuracy([]) == {"intent": 0.0, "urgency": 0.0, "tone": 0.0}
