"""Triage classification eval scorer (CM-30 AC #7).

Jira: CM-30  | Epic: CM-5 (Agent 1 — Enhanced Triage Agent)  | Phase 1

Pure scoring functions over a labelled dataset, kept free of any I/O so they
are importable by both the offline test suite
(``tests/eval/test_triage_eval.py``) and the operator CLI
(``infra/scripts/eval-triage.py``). The CLI supplies a real
:class:`~agents.orchestrator.triage.LLMTriageClassifier`; the tests supply a
fake classifier with known predictions — same scorer, no network.

The AC target is ">90% classification accuracy". We treat **intent** accuracy
as the gate (it drives routing — the AC's "correct downstream agent picked").
``urgency`` and ``tone`` accuracy are computed and reported as secondary
diagnostics but are not gated: they're inherently fuzzier, and gating them
would make the eval flaky against a hand-labelled gold set.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from .triage import TriageClassification

#: AC #7 gate — intent accuracy must exceed this fraction.
INTENT_ACCURACY_TARGET: float = 0.90

#: Fields scored for accuracy. ``intent`` is the gate; the rest are diagnostics.
SCORED_FIELDS: tuple[str, ...] = ("intent", "urgency", "tone")


class _Classifier(Protocol):
    """Minimal classifier shape the eval needs (see ``triage.TriageClassifier``)."""

    def classify(self, message: str, history: list[dict[str, Any]]) -> TriageClassification: ...


@dataclass(frozen=True)
class ExampleResult:
    """One scored example — prediction vs. reference, per field."""

    message: str
    predicted: dict[str, str]
    reference: dict[str, str]
    correct: dict[str, bool]


@dataclass
class EvalReport:
    """Aggregate eval outcome over a dataset."""

    n: int
    accuracy: dict[str, float]
    results: list[ExampleResult] = field(default_factory=list)

    @property
    def intent_accuracy(self) -> float:
        return self.accuracy.get("intent", 0.0)

    @property
    def passed(self) -> bool:
        """True iff intent accuracy clears the AC #7 gate."""
        return self.intent_accuracy > INTENT_ACCURACY_TARGET

    def mismatches(self, dimension: str = "intent") -> list[ExampleResult]:
        """Examples the classifier got wrong on ``dimension`` — for debugging."""
        return [r for r in self.results if not r.correct.get(dimension, True)]


def score_classification(
    predicted: TriageClassification, reference: dict[str, Any]
) -> dict[str, bool]:
    """Per-field correctness of one prediction against its reference labels.

    Only fields present in ``reference`` are scored. Comparison is on the
    enum's string value so a ``TriageClassification`` (enum-typed) and the
    JSON reference (plain strings) compare cleanly.
    """
    pred = {
        "intent": predicted.intent.value,
        "urgency": predicted.urgency.value,
        "tone": predicted.tone.value,
    }
    return {
        f: pred[f] == reference[f] for f in SCORED_FIELDS if f in reference
    }


def accuracy(results: Sequence[ExampleResult]) -> dict[str, float]:
    """Per-field accuracy across results. Empty input -> all-zero (no examples)."""
    out: dict[str, float] = {}
    for f in SCORED_FIELDS:
        scored = [r.correct[f] for r in results if f in r.correct]
        out[f] = (sum(scored) / len(scored)) if scored else 0.0
    return out


def run_eval(
    classifier: _Classifier, examples: Iterable[dict[str, Any]]
) -> EvalReport:
    """Run ``classifier`` over labelled ``examples`` and aggregate accuracy.

    Each example is the seed-file shape::

        {"inputs": {"message": "...", "tenant_id": "..."},
         "outputs": {"intent": "...", "urgency": "...", "tone": "..."}}

    History is empty for the eval — the seed set scores classification on the
    message alone (history-driven follow-up recognition is exercised by the
    unit tests, not the accuracy gate).
    """
    results: list[ExampleResult] = []
    for ex in examples:
        message = ex["inputs"]["message"]
        reference = ex["outputs"]
        prediction = classifier.classify(message, [])
        correct = score_classification(prediction, reference)
        results.append(
            ExampleResult(
                message=message,
                predicted={
                    "intent": prediction.intent.value,
                    "urgency": prediction.urgency.value,
                    "tone": prediction.tone.value,
                },
                reference={k: reference[k] for k in SCORED_FIELDS if k in reference},
                correct=correct,
            )
        )
    return EvalReport(n=len(results), accuracy=accuracy(results), results=results)
