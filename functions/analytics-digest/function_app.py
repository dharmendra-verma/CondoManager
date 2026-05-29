"""Azure Functions Timer trigger — weekly analytics digest (CM-36).

Jira: CM-36  | Epic: CM-Epic 11 (Analytics)  | Phase 1

A thin shell over ``agents.analytics.run_weekly_digest``: it sets up structured
logging + a correlation id and runs one digest pass every Monday at 08:00 UTC
(``DIGEST_SCHEDULE`` below). ``run_weekly_digest`` is non-blocking — it logs and
returns a (possibly empty) report rather than raising into the timer.

All business logic + tests live in ``agents/analytics/`` — this file is
deploy-time glue and is intentionally trivial. The ``agents`` package is bundled
into the deployment (see ``.funcignore`` / ``requirements.txt``).

Environment (set by ``infra/bicep/modules/analytics-functions.bicep``):

* ``COSMOS_ENDPOINT``   Cosmos account endpoint (reads the ``tickets`` container)
* ``DIGEST_RECIPIENTS`` comma-separated board recipients (used once real
                        email delivery lands; the logging default ignores it)
* ``ENVIRONMENT``       ``dev`` | ``prod`` (log tagging)
"""

from __future__ import annotations

import logging
import os

import azure.functions as func
from agents.analytics import run_weekly_digest
from agents.observability import configure_logging, with_request_id

app = func.FunctionApp()

_log = logging.getLogger("analytics_digest.function")

# Every Monday at 08:00 UTC (NCRONTAB: sec min hour day month day-of-week).
DIGEST_SCHEDULE = "0 0 8 * * 1"


@app.function_name(name="analytics_digest")
@app.timer_trigger(
    arg_name="timer",
    schedule=DIGEST_SCHEDULE,
    run_on_startup=False,
    use_monitor=True,
)
def analytics_digest(timer: func.TimerRequest) -> None:
    """Run one weekly-digest pass. Non-blocking; skips cleanly if unconfigured."""
    environment = os.environ.get("ENVIRONMENT", "dev")
    configure_logging(service_name="analytics-digest", environment=environment)

    with with_request_id():
        report = run_weekly_digest()
        _log.info(
            "analytics_digest complete tickets=%d recurring=%d predictions=%d notes=%d",
            report.ticket_count,
            len(report.recurring),
            len(report.predictions),
            len(report.notes),
        )
