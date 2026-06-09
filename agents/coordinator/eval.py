"""Coordinator trajectory + sub-task coverage eval (CM-90, Track B).

Jira: CM-90  | Epic: Track B (Coordinator / multi-intent orchestration)  | Phase 1

The Coordinator's value claim — *compound requests no longer drop sub-tasks* —
needs a different eval from the golden-label classification scorers: the
trajectory is non-deterministic, so we score **sub-task coverage** (the headline,
deterministic number) and, as a diagnostic, an **LLM-judge** on plan sensibility
and answer quality. This mirrors the CM-30/CM-39 triage eval split
(:mod:`agents.orchestrator.eval`): coverage is the gate, the judge is advisory.

Pure scoring — no I/O, no model construction — so it is importable by both the
offline test suite (``tests/coordinator/test_eval_coordinator.py``, which injects
the deterministic ``StubCoordinatorPlanner`` + template synthesizer) and the
operator CLI (``infra/scripts/eval-coordinator.py``, which injects the real LLM
planner + an LLM judge). ``CoordinatorResult`` / ``SynthesisResult`` are typing-
only imports so this module stays import-cheap and offline-safe.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # typing-only — keeps the module import-cheap / offline-safe
    from agents.coordinator.planner import CoordinatorResult
    from agents.coordinator.synthesis import SynthesisResult

#: Offline gate (AC #2) — the StubPlanner trajectories must address every expected
#: sub-task, i.e. mean coverage == 1.0.
COVERAGE_TARGET: float = 1.0


def _obs_status(obs: dict[str, Any]) -> str | None:
    """The sub-result's status (top-level or nested under ``output``)."""
    res = obs.get("result") if isinstance(obs.get("result"), dict) else {}
    res = res or {}
    nested = res.get("output") if isinstance(res.get("output"), dict) else {}
    return res.get("status") or (nested or {}).get("status")


def _fired_tools(result: CoordinatorResult) -> set[str]:
    """Specialist tools the trajectory actually invoked (excluding internal skips).

    A ``skipped`` observation (e.g. ``vendor_agent`` with no prior ticket) is an
    internal no-op, not a handled sub-task, so it is excluded.
    """
    return {
        obs["tool"]
        for obs in result.sub_results
        if isinstance(obs, dict) and obs.get("tool") and _obs_status(obs) != "skipped"
    }


def addressed_tools(result: CoordinatorResult, synthesis: SynthesisResult) -> set[str]:
    """Tools that fired AND are represented in the synthesized reply.

    A refused / ``no_vendor`` leg still counts as *addressed* — the sub-task was
    routed to a specialist and is reflected in the reply (as an unresolved note),
    which is precisely the "no longer dropped" property CM-90 measures. Only an
    internal ``skipped`` no-op is excluded.
    """
    in_reply = {p.get("tool") for p in synthesis.parts if p.get("tool")}
    return {tool for tool in _fired_tools(result) if tool in in_reply}


def subtask_coverage(
    result: CoordinatorResult,
    synthesis: SynthesisResult,
    expected_tools: Iterable[str],
) -> float:
    """Fraction of expected sub-tasks addressed in the final reply (0.0–1.0)."""
    expected = set(expected_tools)
    if not expected:
        return 1.0
    return len(expected & addressed_tools(result, synthesis)) / len(expected)


def tool_selection_exact(
    result: CoordinatorResult, expected_tools: Iterable[str]
) -> bool:
    """True iff the trajectory fired exactly the expected tool set (order-free).

    The offline stub assertion (AC #3): the deterministic planner must select
    precisely the tools the golden label expects — no extras, no misses.
    """
    return _fired_tools(result) == set(expected_tools)


@dataclass(frozen=True)
class CoverageResult:
    """One scored example — coverage + tool-selection exactness."""

    message: str
    expected_tools: list[str]
    addressed_tools: list[str]
    coverage: float
    tool_exact: bool


@dataclass
class CoverageReport:
    """Aggregate coverage outcome over the golden set."""

    n: int
    mean_coverage: float
    results: list[CoverageResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True iff mean coverage clears the AC #2 gate."""
        return self.mean_coverage >= COVERAGE_TARGET

    def mismatches(self) -> list[CoverageResult]:
        """Examples below full coverage or with a wrong tool set — for debugging."""
        return [r for r in self.results if r.coverage < COVERAGE_TARGET or not r.tool_exact]


class TrajectoryRunner(Protocol):
    """Runs one golden example to a ``(CoordinatorResult, SynthesisResult)`` pair.

    The offline test injects the stub planner + template synthesizer; the CLI
    injects the LLM planner + the env-selected synthesizer — same scorer, no
    network in the offline path.
    """

    def __call__(
        self, example: dict[str, Any]
    ) -> tuple[CoordinatorResult, SynthesisResult]: ...


def run_coverage_eval(
    runner: TrajectoryRunner, examples: Iterable[dict[str, Any]]
) -> CoverageReport:
    """Run ``runner`` over labelled ``examples`` and aggregate sub-task coverage.

    Each example is the seed-file shape::

        {"inputs": {"message": "...", "tenant_id": "...", "sub_intents": [...]},
         "outputs": {"expected_subtasks": [...], "expected_tools": [...]}}
    """
    results: list[CoverageResult] = []
    for ex in examples:
        expected = list(ex["outputs"]["expected_tools"])
        result, synthesis = runner(ex)
        results.append(
            CoverageResult(
                message=ex["inputs"]["message"],
                expected_tools=expected,
                addressed_tools=sorted(addressed_tools(result, synthesis)),
                coverage=subtask_coverage(result, synthesis, expected),
                tool_exact=tool_selection_exact(result, expected),
            )
        )
    mean = (sum(r.coverage for r in results) / len(results)) if results else 0.0
    return CoverageReport(n=len(results), mean_coverage=mean, results=results)


class TrajectoryJudge(Protocol):
    """An LLM-judge that scores a trajectory's plan + answer quality (diagnostic).

    Concrete implementations (e.g. the GPT-4o-mini judge in the operator CLI) live
    behind ``OPENAI_API_KEY``; the offline suite never constructs one. Kept out of
    the coverage gate on purpose — judge variance must not flake CI (the same
    rationale as the triage ``urgency``/``tone`` diagnostics).
    """

    def score(
        self, *, message: str, result: CoordinatorResult, synthesis: SynthesisResult
    ) -> float: ...
