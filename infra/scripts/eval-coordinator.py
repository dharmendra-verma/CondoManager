#!/usr/bin/env python3
"""Run the live Coordinator trajectory eval — sub-task coverage + LLM-judge (CM-90).

Jira: CM-90  | Epic: Track B (Coordinator / multi-intent orchestration)  | Phase 1

Operator-run, post-deploy (mirrors infra/scripts/eval-triage.py). Loads the
compound-request golden set (default: tests/eval/coordinator_seed.jsonl), runs the
**real** LLMCoordinatorPlanner + synthesizer over every example, and reports:

  * **sub-task coverage** — % of expected sub-tasks addressed in the final reply
    (the headline "compound requests no longer drop sub-tasks" metric). This is
    the GATE: exits 1 if mean coverage < --min-coverage.
  * **tool-selection exactness** — did the trajectory fire exactly the labelled
    tools (diagnostic).
  * **LLM-judge** — a GPT-4o-mini score on plan sensibility + final-answer quality
    (diagnostic only, never gated — mirrors the triage urgency/tone split).

The offline deterministic gate (coverage == 100% on the StubPlanner) lives in
tests/coordinator/test_eval_coordinator.py and runs in CI with no key.

Usage::

    export OPENAI_API_KEY=<key>
    python infra/scripts/eval-coordinator.py
    python infra/scripts/eval-coordinator.py --seed-file <path> --limit 20 --min-coverage 0.9 --no-judge

Exit codes: 0 = mean coverage >= --min-coverage; 1 = below target or misconfiguration.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# Make `agents` importable when run as a plain script from the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from agents.coordinator.eval import (  # noqa: E402
    COVERAGE_TARGET,
    subtask_coverage,
    tool_selection_exact,
)
from agents.coordinator.planner import (  # noqa: E402
    SECRET_PLACEHOLDER,
    CoordinatorResult,
    LLMCoordinatorPlanner,
)
from agents.coordinator.synthesis import SynthesisResult, get_synthesizer  # noqa: E402
from agents.orchestrator.state import AgentState  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the CM-90 Coordinator trajectory eval.")
    p.add_argument(
        "--seed-file",
        default=str(_REPO_ROOT / "tests" / "eval" / "coordinator_seed.jsonl"),
        help="JSONL golden set. Default: tests/eval/coordinator_seed.jsonl",
    )
    p.add_argument("--limit", type=int, default=0, help="Score only the first N (0 = all).")
    p.add_argument(
        "--min-coverage",
        type=float,
        default=0.9,
        help="Gate: exit 1 if mean coverage falls below this (default 0.9).",
    )
    p.add_argument("--no-judge", action="store_true", help="Skip the LLM-judge diagnostic.")
    p.add_argument("--show-misses", action="store_true", help="Print sub-coverage examples.")
    return p.parse_args()


def _load_examples(path: Path) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line_num, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                examples.append(json.loads(line))
            except json.JSONDecodeError as e:
                sys.stderr.write(f"FAIL: {path}:{line_num} is not valid JSON: {e}\n")
                raise SystemExit(1) from e
    return examples


def _run_example(example: dict[str, Any]) -> tuple[CoordinatorResult, SynthesisResult]:
    """Run one example through the real LLM planner + env-selected synthesizer."""
    inp = example["inputs"]
    state = AgentState(
        tenant_id=inp.get("tenant_id", "t-eval"),
        request_id="r-eval",
        raw_message=inp["message"],
        sub_intents=inp.get("sub_intents", []),
    )
    result = LLMCoordinatorPlanner().run(state)
    return result, get_synthesizer().synthesize(result.sub_results)


class _LLMTrajectoryJudge:
    """GPT-4o-mini judge — scores plan sensibility + answer quality in [0, 1]."""

    def __init__(self, model: str = "gpt-4o-mini") -> None:
        from langchain_openai import ChatOpenAI  # noqa: PLC0415  (lazy by design)
        from pydantic import BaseModel, Field  # noqa: PLC0415

        class _Score(BaseModel):
            score: float = Field(
                ge=0.0, le=1.0,
                description="Overall quality of the plan + final reply, 0 (bad) to 1 (excellent).",
            )

        self._schema = _Score
        self._llm: Any = ChatOpenAI(model=model, temperature=0.0).with_structured_output(_Score)

    def score(
        self, *, message: str, result: CoordinatorResult, synthesis: SynthesisResult
    ) -> float:
        from langchain_core.messages import HumanMessage, SystemMessage  # noqa: PLC0415

        tools = ", ".join(o.get("tool", "?") for o in result.sub_results) or "(none)"
        system = SystemMessage(
            content=(
                "You are evaluating a condo-management coordinator. Given the tenant "
                "message, the tools it called, and the final reply, rate 0..1 whether "
                "the plan was sensible (right specialists, no missed sub-task, no "
                "over-calling) AND the reply addresses every sub-task coherently."
            )
        )
        human = HumanMessage(
            content=f"Tenant message:\n{message}\n\nTools called: {tools}\n\nFinal reply:\n{synthesis.reply}"
        )
        out = self._llm.invoke([system, human])
        return float(out.score)


def main() -> int:
    args = parse_args()

    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key or key == SECRET_PLACEHOLDER:
        sys.stderr.write(
            "OPENAI_API_KEY is missing or holds REPLACE-ME. Populate the key "
            "(see docs/TRIAGE.md), export it locally, and re-run.\n"
        )
        return 1

    seed_path = Path(args.seed_file)
    if not seed_path.is_file():
        sys.stderr.write(f"Seed file not found: {seed_path}\n")
        return 1
    examples = _load_examples(seed_path)
    if args.limit > 0:
        examples = examples[: args.limit]
    if not examples:
        sys.stderr.write(f"No examples to score in {seed_path}.\n")
        return 1

    judge = None if args.no_judge else _LLMTrajectoryJudge()

    coverages: list[float] = []
    exacts = 0
    judge_scores: list[float] = []
    misses: list[tuple[str, float, list[str]]] = []

    for ex in examples:
        expected = list(ex["outputs"]["expected_tools"])
        result, synthesis = _run_example(ex)
        cov = subtask_coverage(result, synthesis, expected)
        coverages.append(cov)
        if tool_selection_exact(result, expected):
            exacts += 1
        if cov < COVERAGE_TARGET:
            fired = [o.get("tool") for o in result.sub_results]
            misses.append((ex["inputs"]["message"], cov, fired))
        if judge is not None:
            judge_scores.append(judge.score(message=ex["inputs"]["message"], result=result, synthesis=synthesis))

    n = len(examples)
    mean_cov = sum(coverages) / n
    print(f"Coordinator eval — {n} examples")
    print(f"  sub-task coverage : {mean_cov:.1%}  (gate: >= {args.min_coverage:.0%})")
    print(f"  tool-selection exact: {exacts}/{n}  (diagnostic)")
    if judge_scores:
        print(f"  LLM-judge (avg)   : {sum(judge_scores) / len(judge_scores):.2f}  (diagnostic)")

    if args.show_misses:
        for msg, cov, fired in misses:
            print(f"  MISS cov={cov:.0%} fired={fired} :: {msg!r}")

    if mean_cov >= args.min_coverage:
        print(f"\nPASS — coverage {mean_cov:.1%} >= {args.min_coverage:.0%}")
        return 0
    print(f"\nFAIL — coverage {mean_cov:.1%} below the {args.min_coverage:.0%} gate")
    return 1


if __name__ == "__main__":  # pragma: no cover  (manual operator run)
    sys.exit(main())
