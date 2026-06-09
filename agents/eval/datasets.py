"""Golden-dataset locations + loader for the unified eval suite (CM-39).

The 5 per-agent golden sets live under ``tests/eval/`` (added by CM-30/31/32/
33/35). CM-39 reuses them verbatim — the suite is an aggregator, not a new set
of fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# agents/eval/datasets.py -> parents[2] is the repo root.
_EVAL_DIR = Path(__file__).resolve().parents[2] / "tests" / "eval"

TRIAGE_SEED = _EVAL_DIR / "triage_seed.jsonl"
ESCALATION_SEED = _EVAL_DIR / "escalation_seed.jsonl"
KNOWLEDGE_SEED = _EVAL_DIR / "knowledge_seed.jsonl"
# CM-85: multi-hop trajectory eval set (>=2-source / reformulation questions).
KNOWLEDGE_MULTIHOP_SEED = _EVAL_DIR / "knowledge_multihop_seed.jsonl"
DEDUP_SEED = _EVAL_DIR / "maintenance_dedup_seed.jsonl"
VENDOR_SEED = _EVAL_DIR / "vendor_match_seed.jsonl"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a ``.jsonl`` golden set into a list of dicts (blank lines skipped)."""
    if not path.exists():
        raise FileNotFoundError(f"eval dataset missing: {path}")
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
