"""Azure Functions Timer trigger — weekly Analytics digest (CM-36).

Jira: CM-36  | Epic: CM-11 (Agent 7 — Analytics & Forecasting)  | Phase 3

A thin shell over ``agents.analytics.run_digest`` — exactly the CM-34
gdrive-sync shape: set up structured logging + a correlation id, wire the real
Cosmos reader / digest store / Slack notifier from environment config, and run
one digest pass every Monday morning. All logic + tests live in
``agents/analytics/``.

Environment (set as Function App settings / Key Vault references by
``infra/bicep/modules/analytics.bicep``):

* ``COSMOS_ENDPOINT``        Cosmos account endpoint (reads tickets + escalations)
* ``SLACK_WEBHOOK_URL`` (KV) manager Slack channel for the digest
* ``ANALYTICS_TENANT_ID``    tenant whose digest to build (default ``"default"``)
* ``ENVIRONMENT``            ``dev`` | ``prod`` (log tagging)
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

import azure.functions as func
from agents.analytics import (
    get_analytics_reader,
    get_digest_notifier,
    get_digest_store,
    run_digest,
)
from agents.observability import configure_logging, with_request_id, with_tenant

app = func.FunctionApp()

_log = logging.getLogger("analytics_digest.function")

# Mondays at 08:00 UTC (NCRONTAB: sec min hour day month day-of-week; 1 = Monday).
DIGEST_SCHEDULE = "0 0 8 * * 1"


@app.function_name(name="analytics_digest")
@app.timer_trigger(
    arg_name="timer",
    schedule=DIGEST_SCHEDULE,
    run_on_startup=False,
    use_monitor=True,
)
def analytics_digest(timer: func.TimerRequest) -> None:
    """Build + deliver one weekly digest. Logs and skips cleanly if unconfigured."""
    environment = os.environ.get("ENVIRONMENT", "dev")
    configure_logging(service_name="analytics-digest", environment=environment)

    tenant_id = os.environ.get("ANALYTICS_TENANT_ID", "default")
    reader = get_analytics_reader()
    store = get_digest_store()
    notifier = get_digest_notifier()  # always returns a notifier (Slack or log)

    if reader is None or store is None:
        # Provisioned before COSMOS_ENDPOINT is seeded — surface once per tick.
        _log.warning("analytics_digest skipped — unconfigured: COSMOS_ENDPOINT")
        return

    with with_request_id(), with_tenant(tenant_id):
        report = run_digest(
            tenant_id=tenant_id,
            reader=reader,
            digest_store=store,
            notifier=notifier,
            now=datetime.now(UTC),
        )
        _log.info(
            "analytics_digest complete recurring=%d predictions=%d "
            "escalations=%d notified=%s",
            report.recurring_count,
            report.prediction_count,
            report.escalation_count,
            report.notified,
        )
