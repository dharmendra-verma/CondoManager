"""``agents.eval`` — unified offline PRD eval suite (CM-39).

Aggregates the per-agent golden datasets + scorers (CM-30/31/32/33/35) into one
:func:`run_suite` → :class:`SuiteReport` (the PRD scorecard). Used by
``tests/eval/test_eval_suite.py`` (CI gate) and ``infra/scripts/run-eval-suite.py``
(operator CLI). Import-cheap: agent SDKs are lazy-imported per case.
"""

from __future__ import annotations

from agents.eval.suite import CaseResult, MetricResult, SuiteReport, run_suite

__all__ = ["CaseResult", "MetricResult", "SuiteReport", "run_suite"]
