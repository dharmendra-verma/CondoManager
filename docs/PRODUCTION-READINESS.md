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
| Verification accuracy 95% (dedup) | `maintenance.dedup_precision > 0.95` ✅ gated | `metric.maintenance.dedup` ⏳ | ✅ gated; runtime emit = follow-up |
| Auto-dispatch > 50% | `vendor.match_accuracy ≥ 0.90` ✅ gated | `metric.vendor.auto_dispatch` / `.hitl` ⏳ | ◐ matching gated; dispatch-rate emit = follow-up |
| Legal-flag recall 100% | `escalation.legal_recall ≥ 1.0` ✅ gated | `metric.escalation.legal_flag` ⏳ | ✅ gated; runtime emit = follow-up |
| Ack latency < 2s | n/a (latency, not accuracy) | `metric.ack_latency_ms` ⏳ | ⏳ entry-layer instrumentation = follow-up |
| TTM reduction 80% | n/a — needs resolved-ticket lifecycle | `metric.ttm_*` ⏳ | ⏳ baseline pending data |
| Follow-up reduction 50% | n/a — needs longitudinal data | `metric.followup_*` ⏳ | ⏳ baseline pending data |
| Efficiency gain 30% | n/a — needs manager time study | — | ⏳ baseline pending data |

Legend: ✅ done in CM-39 · ◐ partial · ⏳ follow-up sub-task (see §5).

## 3. Online feedback loop (manager HITL ratings)

The `hitl_review` interrupt (CM-32) is the capture point: when a manager
approves/rejects an escalation draft, the decision + optional rating/comment is
recorded and an `metric.hitl.rating` event emitted, so approval rate and
reviewer load are dashboardable. **Status: ⏳ follow-up sub-task** (CM-39 ships
the event name + the persistence seam; wiring the resume handler is split out).

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

- **Metric wiring breadth** — emit `metric.maintenance.dedup`,
  `metric.vendor.{auto_dispatch,hitl}`, `metric.escalation.legal_flag`, and
  `metric.ack_latency_ms` at the remaining decision/entry points + tests.
- **HITL ratings persistence** — record manager `hitl_review` decisions to the
  `escalations` store + emit `metric.hitl.rating`.
- **Dashboards** — add the PRD-metrics workbook panel group + the Langfuse
  dashboards; generalize `seed-langsmith-dataset.py` to all 5 datasets.
- **Outcome metrics baselines** — TTM, follow-up reduction, efficiency gain
  once resolved-ticket lifecycle data is captured.

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
