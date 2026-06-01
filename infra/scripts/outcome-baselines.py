#!/usr/bin/env python3
"""Compute the CM-46 outcome-metric baselines (TTM + follow-up rate).

These are the PRD outcome metrics that need resolved-ticket lifecycle data:
time-to-mitigate (target 80% reduction) and follow-up rate (target 50%
reduction). This script reads the CM-31 ``tickets`` container for one tenant and
prints the current baseline so the reduction is measurable as resolutions
accrue.

Offline-safe: with no ``COSMOS_ENDPOINT`` the analytics reader is unavailable,
so the script reports "pending data" and exits 0 (nothing to baseline yet) — it
never fabricates a number. Efficiency-gain (the third outcome metric) is a
manual manager time study and is intentionally NOT computed here; its emission
contract is documented in docs/PRODUCTION-READINESS.md.

    python infra/scripts/outcome-baselines.py --tenant t-acme --window-days 30
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agents.analytics import followup_rate, get_analytics_reader, ttm_baseline  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="CondoManager outcome baselines (CM-46).")
    parser.add_argument("--tenant", required=True, help="Tenant id to baseline.")
    parser.add_argument(
        "--window-days", type=int, default=30, help="Trailing window in days (default 30)."
    )
    args = parser.parse_args()

    reader = get_analytics_reader()
    if reader is None:
        print(
            "outcome baselines: COSMOS_ENDPOINT not set - no resolved-ticket data "
            "available. TTM + follow-up baselines: pending data."
        )
        return 0

    now = datetime.now(UTC)
    since = now - timedelta(days=args.window_days)
    tickets = reader.recent_tickets(args.tenant, since=since)

    ttm = ttm_baseline(tickets, now=now, window_days=args.window_days)
    fu = followup_rate(tickets, now=now, window_days=args.window_days)

    print(f"Outcome baselines — tenant={args.tenant}, window={args.window_days}d")
    print("-" * 60)
    if ttm.n_resolved == 0:
        print("TTM (time-to-mitigate): pending data (0 resolved tickets in window)")
    else:
        print(
            f"TTM (time-to-mitigate): median={ttm.median_hours}h "
            f"p90={ttm.p90_hours}h over {ttm.n_resolved} resolved"
        )
    if fu.n_resolved == 0:
        print("Follow-up rate: pending data (0 resolved tickets in window)")
    else:
        print(
            f"Follow-up rate: {fu.rate:.1%} "
            f"({fu.n_followups}/{fu.n_resolved} resolved saw a recurrence)"
        )
    print("Efficiency gain: pending manual manager time study (see PRODUCTION-READINESS.md)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
