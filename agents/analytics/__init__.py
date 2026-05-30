"""``agents.analytics`` — weekly Analytics & Forecasting digest (CM-36).

Jira: CM-36  | Epic: CM-11 (Agent 7 — Analytics & Forecasting)  | Phase 3

A scheduled (weekly) Azure Functions Timer job reads the CM-31 ``tickets`` and
CM-32 ``escalations`` from Cosmos, computes deterministic analytics (recurring
issues, contractor scoring, sentiment trend, predictive flags), persists a
:class:`WeeklyDigest` to the ``digests`` container for the CM-37 tenant portal,
and posts the digest to the manager Slack channel. NOT a LangGraph node — an
offline batch job, mirroring CM-34's ``agents.knowledge`` package shape.

The package is import-cheap (azure-cosmos / httpx are lazy-imported in the
seam that uses them) so ``import agents.analytics`` works in the test env.

Public surface:

* :func:`run_digest` — one weekly pass (the orchestrator).
* :func:`build_digest` / :func:`render_digest_text` — pure digest assembly.
* :func:`detect_recurring` / :func:`score_contractors` / :func:`sentiment_trend`
  / :func:`predictive_flags` — the pure analyzers.
* Seams: :func:`get_analytics_reader`, :func:`get_digest_store`,
  :func:`get_digest_notifier` (env-gated, with in-memory/log fallbacks).
* Models: :class:`WeeklyDigest`, :class:`DigestReport`, :class:`EscalationEvent`, …
"""

from __future__ import annotations

from .analyze import (
    detect_recurring,
    predictive_flags,
    score_contractors,
    sentiment_trend,
)
from .digest import build_digest, render_digest_text
from .models import (
    ContractorScore,
    DigestReport,
    EscalationEvent,
    PredictiveFlag,
    RecurringIssue,
    SentimentPoint,
    SentimentTrend,
    WeeklyDigest,
)
from .repository import (
    AnalyticsReader,
    CosmosAnalyticsReader,
    CosmosDigestStore,
    DigestNotifier,
    DigestStore,
    InMemoryAnalyticsReader,
    LogDigestNotifier,
    NoopDigestStore,
    SlackDigestNotifier,
    get_analytics_reader,
    get_digest_notifier,
    get_digest_store,
)
from .run import run_digest

__all__ = [
    "AnalyticsReader",
    "ContractorScore",
    "CosmosAnalyticsReader",
    "CosmosDigestStore",
    "DigestNotifier",
    "DigestReport",
    "DigestStore",
    "EscalationEvent",
    "InMemoryAnalyticsReader",
    "LogDigestNotifier",
    "NoopDigestStore",
    "PredictiveFlag",
    "RecurringIssue",
    "SentimentPoint",
    "SentimentTrend",
    "SlackDigestNotifier",
    "WeeklyDigest",
    "build_digest",
    "detect_recurring",
    "get_analytics_reader",
    "get_digest_notifier",
    "get_digest_store",
    "predictive_flags",
    "render_digest_text",
    "run_digest",
    "score_contractors",
    "sentiment_trend",
]
