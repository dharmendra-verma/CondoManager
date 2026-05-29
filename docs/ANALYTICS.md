# Analytics Agent — `agents/analytics/` (CM-36)

> Jira: **CM-36** | Epic: CM-Epic 11 (Analytics) | Phase 1

A weekly **batch job** (Azure Functions Timer) over the CM-31 `tickets`
container. Unlike Triage/Maintenance/Vendor/Escalation it is **not** a LangGraph
node — it runs on a schedule, computes building-health analytics, and delivers a
board digest. All logic is deterministic and offline-testable; the heavy SDK
(`azure-cosmos`) is lazy-imported.

## Pipeline

```
weekly timer (Mon 08:00 UTC)
  -> AnalyticsSource.list_tickets(since = now - 30d)   # Cosmos or in-memory
  -> recurring + contractor + sentiment + predictive   # pure functions
  -> build_digest(...) -> DigestReport (+ rendered body)
  -> DigestDelivery.deliver(report)                    # logging default
```

`run_weekly_digest` is **non-blocking** (AC6): any source/compute/delivery
failure is logged and a (possibly empty) report is returned — an exception
never escapes into the timer.

## Computations

| AC | Module | What |
|----|--------|------|
| AC1 | `recurring.py` | `(unit, category)` with **> 3** tickets in 30 days. |
| AC2 | `performance.py` | Per-vendor resolution rate + avg response time. |
| AC3 | `sentiment.py` | Week-over-week mean tone score per building. |
| AC4 | `predictive.py` | Threshold rules (e.g. ≥ 3 HVAC/unit in 14 days → boiler service). |
| AC5 | `digest.py` + `delivery.py` | Compose + deliver the digest. |
| AC6 | `functions/analytics-digest/` | Weekly Functions Timer trigger. |

`building = tenant_id`, `location = unit` (tickets carry no building field).

## ⚠️ Data-coverage gaps (honest by design)

Two computations need fields **not yet persisted** on the ticket:

* **Contractor performance (AC2)** needs `vendor_id` + resolution timestamps.
  CM-35 computes `vendor_id` into `state.output` but does not persist it onto
  the ticket doc; resolution time is approximated by `updated_at` when
  `status == Resolved`.
* **Sentiment (AC3)** needs `tone`. CM-30 classifies it but does not persist it.

The engine implements + fixture-tests the full logic. The live Cosmos source
fills what exists and leaves the rest `None`; the scorers emit an explicit
`insufficient_data` / empty result and the digest records a **note** rather than
inventing numbers. Two follow-ups close the loop: persist `vendor_id` on
dispatch (CM-35) and persist `tone` on the ticket.

## Seams (env-gated, like `get_checkpointer`)

* `get_analytics_source()` — `CosmosAnalyticsSource` when `COSMOS_ENDPOINT` is
  real, else an empty `InMemoryAnalyticsSource` (offline/tests).
* `get_digest_delivery()` — `LoggingDigestDelivery` (PII-masked) by default.
  Real email + tenant-portal delivery (the portal lands in CM-37) is deferred.

## Deploy

`infra/bicep/modules/analytics-functions.bicep` provisions a Y1 Linux Python
3.12 Function App (`func-condomanager-<env>-analytics`) + storage, attached to
the shared CM-18 MI for Cosmos data-plane access. The function code is published
out-of-band:

```bash
# package the agents/ pkg alongside functions/analytics-digest/ then:
func azure functionapp publish func-condomanager-dev-analytics --python
```

App settings (set by the module): `COSMOS_ENDPOINT`, `DIGEST_RECIPIENTS`
(unused until real email lands), `ENVIRONMENT`, `APPLICATIONINSIGHTS_CONNECTION_STRING`.

## Tests

`pytest tests/analytics` — per-computation units, digest composition, the
non-blocking orchestrator, and a recurring-detection eval
(`tests/analytics/test_recurring_eval.py`, accuracy gated at 100% over
`tests/eval/analytics_recurring_seed.jsonl`). `tests/infra/test_bicep_lint.sh`
compiles `analytics-functions.bicep` and asserts the wiring + timer schedule.
