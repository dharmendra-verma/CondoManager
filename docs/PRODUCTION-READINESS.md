# Production-readiness checklist (CM-39)

> Jira: **CM-39** | Epic: CM-Epic 14 (Eval / production readiness) | Phase 2
>
> The go/no-go gate for private beta. Every PRD success metric is listed with
> its **offline gate** (golden-data eval), its **runtime metric** (the prod
> `customEvents` event that measures the same quantity), the **alert** that
> watches it, and current **status**. Sign-off at the bottom.

## 1. The eval suite

`agents.eval.run_suite()` aggregates the five per-agent golden datasets +
scorers into one PRD scorecard. It runs:

- in **CI** on every PR via `tests/eval/test_eval_suite.py` (tagged
  `@pytest.mark.eval`; deterministic stand-ins, no network), and
- on demand via `python infra/scripts/run-eval-suite.py` (add `--live`
  + `OPENAI_API_KEY` to gate the model-dependent metrics too).

```
python infra/scripts/run-eval-suite.py        # prints the PRD scorecard
pytest -m eval -q                              # the CI gate
```

## 2. PRD metric → gate → runtime event → status

| PRD metric | Offline gate (eval suite) | Runtime metric event | Status |
|---|---|---|---|
| Triage accuracy > 90% | `triage.intent_accuracy` (gated **live** only) | `metric.triage.route` | ✅ instrumented; live gate operator-run |
| Self-service > 25% | `knowledge.self_service ≥ 0.25` ✅ gated | `metric.knowledge.answered` | ✅ instrumented + gated |
| Hallucination < 1% | `knowledge.hallucination < 0.01` ✅ gated | `metric.knowledge.refused` | ✅ instrumented + gated |
| Verification accuracy 95% (dedup) | `maintenance.dedup_precision > 0.95` ✅ gated | `metric.maintenance.dedup` | ✅ gated + instrumented (CM-46) |
| Auto-dispatch > 50% | `vendor.match_accuracy ≥ 0.90` ✅ gated | `metric.vendor.auto_dispatch` / `.hitl` | ✅ matching gated + dispatch-rate instrumented (CM-46) |
| Legal-flag recall 100% | `escalation.legal_recall ≥ 1.0` ✅ gated | `metric.escalation.legal_flag` | ✅ gated + instrumented (CM-46) |
| Ack latency < 2s | n/a (latency, not accuracy) | `metric.ack_latency_ms` | ✅ entry-layer instrumented (CM-46) |
| TTM reduction 80% | n/a — needs resolved-ticket lifecycle | `metric.ttm_resolution_ms` | ✅ instrumented (CM-46); baseline accrues as tickets resolve |
| Follow-up reduction 50% | n/a — needs longitudinal data | `metric.followup` | ✅ instrumented (CM-46); baseline accrues as tickets resolve |
| Efficiency gain 30% | n/a — needs manager time study | — | ⏳ emission contract only; baseline pending manual time study |

Legend: ✅ done · ◐ partial · ⏳ pending data / out of scope.

## 3. Online feedback loop (manager HITL ratings)

The `hitl_review` interrupt (CM-32) is the capture point: when a manager
approves/rejects an escalation draft, the decision + optional rating/comment is
recorded and a `metric.hitl.rating` event emitted, so approval rate and
reviewer load are dashboardable. **Status: ✅ implemented (CM-44).** On resume,
`hitl_review` reads the optional `rating` (positive int) + `comment` from the
payload, stamps `rated_at`, persists them onto the reused `escalations` record
via `EscalationStore.record_rating(...)`, and emits `metric.hitl.rating`
(`value` = the rating or `1.0`; attrs `decision` ∈ {approve, reject},
`category`, `legal_risk`, `has_rating`). A bare-bool resume records no rating but
still emits the decision so approval rate is always measurable.

## 4. Dashboards

- **Log Analytics** — the CM-25 workbook (`infra/bicep/modules/workbook-payload.json`)
  carries cost / latency / per-node error / HITL-queue panels. A **PRD-metrics**
  panel group over the new `metric.*` `customEvents` is a follow-up sub-task.
- **Langfuse** — `@observe_node` is wired (CM-24); the per-agent cost/quality/
  latency dashboards are built in the Langfuse UI (see `docs/INFRA.md`).

## 5. Follow-up sub-tasks (scope split per the CM-39 plan)

CM-39 ships the **eval-suite core + metric-emission foundation + this
checklist**. The following are filed as sub-tasks so the core lands clean rather
than as a sprawling under-tested change:

- **Metric wiring breadth** — ✅ done (CM-46): emits `metric.maintenance.dedup`
  (maintenance node), `metric.vendor.{auto_dispatch,hitl}` (vendor node),
  `metric.escalation.legal_flag` (escalation node, only when the semantic flag is
  raised), and `metric.ack_latency_ms` (the `WebAdapter` entry layer) + tests.
- **HITL ratings persistence** — ✅ done (CM-44): `hitl_review` records manager
  decisions + optional rating/comment to the `escalations` store and emits
  `metric.hitl.rating`.
- **Dashboards** — add the PRD-metrics workbook panel group + the Langfuse
  dashboards; generalize `seed-langsmith-dataset.py` to all 5 datasets.
  *(Separate sub-task — NOT in CM-46.)*
- **Outcome metrics baselines** — ✅ done (CM-46): `Ticket.resolved_at` + the
  `TicketRepository.resolve()` seam + `agents.maintenance.resolve_ticket()`
  capture the lifecycle and emit `metric.ttm_resolution_ms`; a fresh ticket
  recurring against a RESOLVED issue emits `metric.followup`. Baselines are
  computed by `agents.analytics.ttm_baseline()` / `followup_rate()` and the
  `infra/scripts/outcome-baselines.py` operator script (reports "pending data"
  on empty input — never a fabricated number; the baseline accrues as
  resolutions occur). **Efficiency gain** stays an emission contract pending a
  manual manager time study — deliberately not faked.

## 6. Operational guardrails (already in place)

- Cost cap $5/request + loop cap 50 (`guardrail.cost_cap` / `guardrail.loop_cap`,
  CM-26 alerts).
- Latency-SLO, guardrail-trip, hallucination-spike alert rules (CM-26).
- PII masking on logs/traces (CM-27); secrets in Key Vault, OIDC-only deploy.

## 7. Sign-off

| Gate | Owner | Status |
|---|---|---|
| Offline eval suite green in CI | platform-team | ☐ |
| Runtime PRD metrics emitting in dev | platform-team | ☐ |
| Dashboards reviewed | product-owner | ☐ |
| Follow-up sub-tasks filed + scheduled | platform-team | ☐ |
| **Private-beta go/no-go** | product-owner | ☐ |
