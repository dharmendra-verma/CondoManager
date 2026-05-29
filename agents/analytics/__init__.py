"""``agents.analytics`` — Analytics Agent: pattern detection + weekly digest (CM-36).

An offline batch job (Azure Functions Timer, see ``functions/analytics-digest/``)
over the CM-31 ``tickets`` container. Computes recurring-issue detection,
contractor performance, sentiment trend, and predictive rules, then composes +
delivers a weekly board digest. All logic is deterministic and offline-testable;
the heavy SDK (azure-cosmos) is lazy-imported in the source module.

Public API:

* :func:`run_weekly_digest` — the timer entrypoint (non-blocking).
* :func:`build_digest` — pure composition over a ticket list.
* Models: :class:`AnalyticsTicket`, :class:`DigestReport`, …
* Seams: :func:`get_analytics_source`, :func:`get_digest_delivery`.
"""

from __future__ import annotations

from .delivery import DigestDelivery, get_digest_delivery
from .digest import build_digest
from .models import (
    AnalyticsTicket,
    ContractorScore,
    DigestReport,
    Prediction,
    RecurringIssue,
    SentimentPoint,
)
from .performance import score_contractors
from .predictive import predict
from .recurring import detect_recurring
from .run import run_weekly_digest
from .sentiment import sentiment_trend
from .source import AnalyticsSource, get_analytics_source

__all__ = [
    "AnalyticsSource",
    "AnalyticsTicket",
    "ContractorScore",
    "DigestDelivery",
    "DigestReport",
    "Prediction",
    "RecurringIssue",
    "SentimentPoint",
    "build_digest",
    "detect_recurring",
    "get_analytics_source",
    "get_digest_delivery",
    "predict",
    "run_weekly_digest",
    "score_contractors",
    "sentiment_trend",
]
