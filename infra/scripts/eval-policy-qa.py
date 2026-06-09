#!/usr/bin/env python3
"""Run the live policy Q&A Knowledge-RAG eval — answer quality + breakdowns (CM-91).

Jira: CM-91  | Epic: Knowledge / RAG eval

Operator-run, post-deploy (mirrors infra/scripts/eval-triage.py). Runs the **real**
Knowledge retrieve→answer path (env-configured vector store + embedder + chat
model) over the policy Q&A golden set (default: tests/eval/policy_qa_seed.jsonl)
and reports:

  * **answer quality** — mean token-F1 of the produced answer vs ``expected_answer``
    (a refusal scores 0). This is the GATE: exits 1 below --min-answer-quality.
  * **self-service** — % of answerable questions answered (not refused).
  * **per-policy** and **per-difficulty** breakdowns + the **multi-hop** slice.

PREREQUISITE: the four source policies (Clubhouse / EV / Pet / Housing) must be
ingested into the vector store first (see infra/scripts/ingest.py, CM-66) and the
embedding + chat env vars configured — otherwise retrieval returns nothing and
every question refuses.

The offline deterministic gate (self-service / hallucination over the lexical
doubles) lives in tests/eval/test_policy_qa_eval.py + the CM-39 eval suite and
runs in CI with no key.

Usage::

    export OPENAI_API_KEY=<key>           # + COSMOS / AZURE_OPENAI_* for store+embedder
    python infra/scripts/eval-policy-qa.py
    python infra/scripts/eval-policy-qa.py --seed-file <path> --min-answer-quality 0.5

Exit codes: 0 = answer quality >= --min-answer-quality; 1 = below target or misconfiguration.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from agents.eval.policy_qa import run_policy_qa_eval  # noqa: E402
from agents.knowledge.embeddings import default_embedder  # noqa: E402
from agents.knowledge.cosmos_store import get_vector_store  # noqa: E402
from agents.knowledge.llm import SECRET_PLACEHOLDER, get_chat_model  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the CM-91 policy Q&A Knowledge eval.")
    p.add_argument(
        "--seed-file",
        default=str(_REPO_ROOT / "tests" / "eval" / "policy_qa_seed.jsonl"),
        help="JSONL golden set. Default: tests/eval/policy_qa_seed.jsonl",
    )
    p.add_argument("--limit", type=int, default=0, help="Score only the first N (0 = all).")
    p.add_argument(
        "--min-answer-quality",
        type=float,
        default=0.5,
        help="Gate: exit 1 if mean answer quality falls below this (default 0.5).",
    )
    return p.parse_args()


def _load(path: Path) -> list[dict[str, Any]]:
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


def main() -> int:
    args = parse_args()

    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key or key == SECRET_PLACEHOLDER:
        sys.stderr.write("OPENAI_API_KEY is missing or holds REPLACE-ME. Export a real key and re-run.\n")
        return 1

    store = get_vector_store()
    embedder = default_embedder()
    if store is None or embedder is None:
        sys.stderr.write(
            "Vector store / embedder not configured. Set COSMOS_ENDPOINT + AZURE_OPENAI_* "
            "and ensure the policies are ingested (infra/scripts/ingest.py).\n"
        )
        return 1

    seed_path = Path(args.seed_file)
    if not seed_path.is_file():
        sys.stderr.write(f"Seed file not found: {seed_path}\n")
        return 1
    rows = _load(seed_path)
    if args.limit > 0:
        rows = rows[: args.limit]
    if not rows:
        sys.stderr.write(f"No examples in {seed_path}.\n")
        return 1

    report = run_policy_qa_eval(rows, store=store, embedder=embedder, model=get_chat_model())

    print(f"Policy Q&A eval — {report.n} questions")
    print(f"  answer quality : {report.answer_quality:.1%}  (gate: >= {args.min_answer_quality:.0%})")
    print(f"  self-service   : {report.self_service:.1%}  (answered / answerable)")
    print(f"  hallucination  : {report.hallucination:.1%}")
    print("  by policy:")
    for policy, g in sorted(report.per_policy.items()):
        print(f"    {policy:<32} n={g.n:<3} self_service={g.self_service:.0%} quality={g.answer_quality:.0%}")
    print("  by difficulty:")
    for diff, g in sorted(report.per_difficulty.items()):
        print(f"    {diff:<12} n={g.n:<3} self_service={g.self_service:.0%} quality={g.answer_quality:.0%}")
    if report.multi_hop.n:
        m = report.multi_hop
        print(f"  multi-hop: n={m.n} self_service={m.self_service:.0%} quality={m.answer_quality:.0%}")

    if report.answer_quality >= args.min_answer_quality:
        print(f"\nPASS — answer quality {report.answer_quality:.1%} >= {args.min_answer_quality:.0%}")
        return 0
    print(f"\nFAIL — answer quality {report.answer_quality:.1%} below the {args.min_answer_quality:.0%} gate")
    return 1


if __name__ == "__main__":  # pragma: no cover  (manual operator run)
    sys.exit(main())
