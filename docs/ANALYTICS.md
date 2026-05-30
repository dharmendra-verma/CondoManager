# Analytics Agent — `agents/analytics/` + `functions/analytics-digest/`

> Jira: **CM-36** | Epic: CM-11 (Agent 7 — Analytics & Forecasting) | Phase 3

A weekly Azure Functions Timer job that turns the maintenance + escalation
history into a board-facing digest. **Not** a LangGraph node — an offline
batch job, mirroring the CM-34 gdrive-sync shape (import-cheap package + thin
Function App + Bicep module).

---

## 1. Flow

`functions/analytics-digest/function_app.py` (Mondays 08:00 UTC) →
`agents.analytics.run_digest(tenant_id, reader, digest_store, notifier, now)`:

1. `reader.recent_tickets` / `recent_escalation_events` over the trailing 30 days
   (`tickets` CM-31 + `escalations` CM-32 containers).
2. `build_digest` → runs the four analyzers → `WeeklyDigest`.
3. `digest_store.save` → upsert to the `digests` Cosmos container (CM-37 portal reads it).
4. `notifier.send(render_digest_text(digest))` → manager Slack channel.
5. Returns a `DigestReport` (run tally) + a structured `analytics.run` log line.

Non-blocking (AC #6): a notifier/store failure is logged + reported, never raised.

---

## 2. The four analyzers (deterministic, no LLM)

| AC | Function | Rule |
|----|----------|------|
| #1 recurring | `detect_recurring` | (unit, category) with **> 3** tickets in **30 days** |
| #2 contractor | `score_contractors` | per `owner`: resolution rate = resolved/assigned; avg response ≈ `updated_at − created_at` over resolved |
| #3 sentiment | `sentiment_trend` | escalation count + severity + legal, bucketed by ISO week; week-over-week `rising`/`falling`/`flat` |
| #4 predictive | `predictive_flags` | (unit, category) with **≥ 3** tickets in **14 days** → proactive-service flag |

All pure functions over input lists → fully unit-tested with synthetic fixtures.

---

## 3. Data-availability caveats (honest scoping)

The persisted CM-31/32 schemas don't carry everything the ACs imply; each is
scoped to what exists, with the richer path noted as a follow-up:

- **Contractor = ticket `owner`.** No Vendor entity (CM-35) and no `resolved_at`
  yet, so response time is the best-effort `updated_at − created_at` over
  resolved tickets; unassigned tickets bucket as `"unassigned"`.
- **Sentiment = escalation proxy.** Tickets don't persist tone, so negative
  sentiment is approximated by escalation volume/severity/legal per week.
- **"Building" = `tenant_id`**; recurrence "same location" = `Ticket.unit`.
- **Delivery = Slack + `digests` container.** No email backend exists yet (Slack
  only); email is a follow-up. The portal (CM-37) renders the `digests` docs.

---

## 4. Seams (env-gated, offline fallbacks)

| Seam | Real (env) | Fallback |
|------|------------|----------|
| `get_analytics_reader()` | `CosmosAnalyticsReader` (`COSMOS_ENDPOINT`) | `None` → job skips (or `InMemoryAnalyticsReader` in tests) |
| `get_digest_store()` | `CosmosDigestStore` (`digests`) | `None` → job skips (or `NoopDigestStore` in tests) |
| `get_digest_notifier()` | `SlackDigestNotifier` (`SLACK_WEBHOOK_URL`) | `LogDigestNotifier` |

A dedicated **text** notifier (not the CM-32 escalation `ManagerNotifier`,
which carries an `EscalationRecord`) so the digest body posts without faking a
record.

---

## 5. Infra

- **`digests` Cosmos container** (CM-36 `cosmos.bicep`): partition `/tenantId`,
  shared throughput, **90-day TTL** (rolling rebuildable view).
- **`analytics.bicep`**: a dedicated Y1 Consumption Linux Function App
  (`func-condomanager-analytics-<env>`), MI-attached, `slack-webhook-url` via KV
  reference. Separate from gdrive-sync for schedule + failure isolation.
- No new KV secret (`slack-webhook-url` exists from CM-32); no new pip deps.

---

## 6. Follow-ups
- Email/SMTP delivery channel.
- `Ticket.resolved_at` + CM-35 Vendor entity → real contractor-performance fidelity.
- Persisted per-message tone → true sentiment (vs the escalation proxy).
- Multi-tenant iteration (today one `ANALYTICS_TENANT_ID` per run).
- CM-37 portal rendering of the `digests` container.
