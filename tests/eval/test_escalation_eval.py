"""Offline escalation eval (CM-32) — dataset integrity + heuristic legal recall.

No OpenAI key required. Verifies the labelled set is well-formed and balanced,
and that the heuristic classifier achieves perfect *recall* on the legal-risk
cases (the AC-critical direction — a missed legal flag is the costly error)
with high specificity. Semantic, beyond-keyword legal detection is the LLM's
job and is checked by the operator live run, not here.
"""

from __future__ import annotations

import json
from pathlib import Path

from agents.orchestrator.escalation import HeuristicEscalationClassifier
from agents.orchestrator.state import EscalationCategory

_SEED = Path(__file__).parent / "escalation_seed.jsonl"
_VALID_CATEGORIES = {e.value for e in EscalationCategory}


def _load() -> list[dict]:
    rows = []
    with _SEED.open(encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def test_dataset_size_and_shape() -> None:
    rows = _load()
    assert len(rows) >= 40
    for i, ex in enumerate(rows, 1):
        assert ex["inputs"]["message"], f"line {i}: empty message"
        assert ex["inputs"]["tenant_id"], f"line {i}: empty tenant_id"
        assert ex["outputs"]["category"] in _VALID_CATEGORIES, f"line {i}: bad category"
        assert isinstance(ex["outputs"]["legal_risk"], bool), f"line {i}: legal_risk not bool"


def test_dataset_covers_categories_and_both_legal_values() -> None:
    rows = _load()
    cats = {r["outputs"]["category"] for r in rows}
    legal_vals = {r["outputs"]["legal_risk"] for r in rows}
    assert cats == _VALID_CATEGORIES
    assert legal_vals == {True, False}


def test_heuristic_legal_recall_is_perfect_and_specific() -> None:
    rows = _load()
    clf = HeuristicEscalationClassifier()
    legal = [r for r in rows if r["outputs"]["legal_risk"] is True]
    non_legal = [r for r in rows if r["outputs"]["legal_risk"] is False]

    recall_hits = sum(
        clf.classify(r["inputs"]["message"], []).legal_risk for r in legal
    )
    spec_hits = sum(
        not clf.classify(r["inputs"]["message"], []).legal_risk for r in non_legal
    )
    # Recall must be perfect — a missed legal flag is the costly miss (AC #6).
    assert recall_hits == len(legal)
    # Specificity high (whole-word matching keeps false positives out).
    assert spec_hits / len(non_legal) >= 0.9
