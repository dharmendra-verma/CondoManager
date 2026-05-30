# Escalation Agent — `agents/orchestrator/escalation.py`

> Jira: **CM-32** | Epic: CM-7 (Agent 4 — Escalation Manager Agent) | Phase 1

The Escalation Agent handles high-emotion, repeat, and legally-risky cases. It
is reached when CM-30 triage classifies `intent=escalation`. The CM-28 spine
already routes `escalation → hitl_review → END`; CM-32 fills the node in.

---

## 1. What it does, in order

The `escalation` node (`agents/orchestrator/nodes.py`):

1. **Guardrail check first** (CM-26/28 Stop Rules).
2. **Sub-classify + legal flag** — `get_escalation_classifier().classify(...)`
   returns an `EscalationClassification`: a `category` and a **semantic**
   `legal_risk` flag (AC #1/#2). GPT-4o-mini when `OPENAI_API_KEY` is set, else
   the offline keyword heuristic.
3. **Build the record** — `build_record(...)` assembles the
   `EscalationRecord`: internal summary, a composed manager alert, and an
   empathetic tenant draft that is **held** (never sent here).
4. **Persist** the record to Cosmos (AC #3) via `get_escalation_store().save(...)`.
5. **Alert the manager** (AC #4) via `get_manager_notifier().notify(...)` —
   best-effort; a webhook failure is logged and never breaks the graph.
6. **Route to `hitl_review`** (AC #5/#6).

`hitl_review` pauses with `interrupt(...)`, surfacing the review context, and
on resume sets the record's terminal status from the approval.

---

## 2. Classification (AC #1/#2)

`EscalationClassification` (Pydantic structured output):

| Field        | Values |
|--------------|--------|
| `category`   | `repeat` / `service_failure` / `safety` / `communication_breakdown` / `multi_issue` / `legal` |
| `legal_risk` | `bool` — set on **meaning** (lawyer/sue/court/health/injury/liability/"my rights"), not just keywords |
| `rationale`  | one-line audit string |

The **LLM** classifier is what makes `legal_risk` semantic — it flags
paraphrased exposure like *"I've spoken to someone about my options"*. The
heuristic fallback is whole-word keyword matching (so `"issue"` is not read as
`"sue"`), used offline; it will miss paraphrases, by design.

---

## 3. The legal gate (AC #6) — structural, not statistical

Two facts make *"100% of legal-flagged cases blocked until a manager approves"*
a hard guarantee rather than a model-accuracy bet:

1. **Every escalation routes through `hitl_review`** (CM-28 topology) — nothing
   reaches `END` without the pause.
2. **The draft is only ever marked `sent` on explicit approval.** `escalation`
   sets `status="pending_review"` and holds the draft. Only `hitl_review`, on a
   resume payload with `approved is True`, transitions to `approved_sent` /
   `sent=True`. There is **no auto-approve path**; anything else →
   `rejected` / `sent=False`.

CM-32 only *gates* the draft — it sends no tenant message. That absence of a
send path is what makes the invariant trivially provable (and tested in
`tests/orchestrator/test_hitl.py`).

---

## 4. Seams (env-gated selectors, mirroring `get_checkpointer()`)

| Seam | Real impl (env) | Offline fallback |
|------|-----------------|------------------|
| `get_escalation_classifier()` | `LLMEscalationClassifier` (`OPENAI_API_KEY`) | `HeuristicEscalationClassifier` |
| `get_escalation_store()` | `CosmosEscalationStore` (`COSMOS_ENDPOINT`, `escalations` container) | `NoopEscalationStore` (in-memory) |
| `get_manager_notifier()` | `SlackWebhookNotifier` (`SLACK_WEBHOOK_URL`) | `LogNotifier` |

All three fall back automatically (and treat the CM-18 `REPLACE-ME`
placeholder as unset), so the suite + `python -m agents.orchestrator.demo` run
with no credentials and never make a network call.

---

## 5. Storage + alerting

- **`escalations` Cosmos container** (CM-32 `cosmos.bicep`): partition
  `/tenantId`, shared throughput, **no TTL** (audit/compliance — legal cases
  must not auto-purge). `EscalationRecord.model_dump()` upserts with
  `id=record_id`.
- **Manager alert**: Slack incoming webhook (`slack-webhook-url` KV secret →
  `SLACK_WEBHOOK_URL`). Email/SMTP is a follow-up.

---

## 6. Eval

- `tests/eval/escalation_seed.jsonl` — labelled `{message → category, legal_risk}`.
- `tests/eval/test_escalation_eval.py` — offline: dataset integrity + **perfect
  heuristic legal recall** (a missed legal flag is the costly error) with high
  specificity. Semantic beyond-keyword detection is verified by the LLM via a
  live operator run.

---

## 7. Follow-ups

- Send the approved draft to the tenant channel (Twilio/Telegram/email).
- Email manager-alert channel alongside Slack.
- Persist the approved reply to the `conversations` container.
