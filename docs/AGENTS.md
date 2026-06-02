# LangGraph Spine — `agents/orchestrator/`

> Jira: **CM-28** | Epic: CM-Epic 4 (LangGraph Orchestrator) | Phase 0

This is the orchestrator: a `StateGraph(AgentState)` with nodes for triage,
knowledge, maintenance, escalation, HITL review, and a guardrail-terminated
terminal. CM-30 / CM-31 / CM-32 replace the stub bodies one at a time without
touching the spine. **`triage` (CM-30, see §3), `maintenance` (CM-31, see §8),
and the `vendor` agent that runs after it (CM-35, see §10) are now real**;
`knowledge` (CM-33, §9) and `escalation` (CM-32) are real too.

The hello-world demo runs without OpenAI credentials. Stub nodes return
trivial state updates and the same run produces traces in both
**App Insights** (via CM-22 Azure Monitor exporter) and **LangSmith**
(via CM-23 LangChain callback) when both backends are configured.

---

## 1. Topology

```
            START
              |
              v
            triage
              |
   +----------+----------+----------+
   |          |          |          |
   v          v          v          v
knowledge maintenance escalation guardrail_terminated
   |          |          |               |
   |          v          |               |
   |        vendor        |               |
   |        /    \        v               |
   |   (auto)  (approve) hitl_review       |
   |      |         \____/  |              |
   +------+----------------+---------------+
                         |
                         v
                        END
```

* **Entry**: `START -> triage` (unconditional).
* **Router**: `agents.orchestrator.graph._router` reads `state.routes[-1]`
  and dispatches. Default is `"triage"` when `routes` is empty.
* **Vendor (CM-35)**: `maintenance -> vendor`. The vendor node either
  auto-dispatches and ends, or routes to `hitl_review` for manager approval
  (`agents.orchestrator.graph._vendor_router` on `routes[-1]`).
* **Stop short-circuit**: Any node whose guardrail trips returns
  `routes=["guardrail_terminated"]` and skips its real work.
* **HITL**: `escalation -> hitl_review -> END`, and `vendor -> hitl_review`
  for manager approval. `hitl_review` calls LangGraph's `interrupt(...)`
  primitive; the graph pauses and resumes via
  `graph.invoke(Command(resume=<payload>), config=...)`.

---

## 2. `AgentState` reference

Pydantic `BaseModel` defined in `agents/orchestrator/state.py`. **14 fields**
(the CM-28 spine fields plus `escalation`, added by CM-32).

> **New here? Read this as a form that travels with one tenant message.** Each
> node fills in a few fields and passes the whole form to the next node. Nothing
> is mutated in place — LangGraph copies the form forward with the node's
> updates. `routes[-1]` is "which node runs next".

| Field          | Type                       | Default          | Notes                                |
|----------------|----------------------------|------------------|--------------------------------------|
| `tenant_id`    | `str`                      | _required_       | Per-tenant scoping for all telemetry. |
| `request_id`   | `str`                      | _required_       | Correlates with CM-21 `request_id` ContextVar. |
| `channel`      | `Channel`                  | `UNKNOWN`        | `whatsapp` / `telegram` / `email` / `web` / `unknown`. |
| `raw_message`  | `str`                      | `""`             | Inbound tenant message — set at the entry adapter. |
| `normalized`   | `NormalizedMessage \| None`| `None`           | Channel-normalized payload (CM-29); see [`docs/CHANNELS.md`](CHANNELS.md). |
| `intent`       | `Intent \| None`           | `None`           | `maintenance` / `inquiry` / `escalation` / `follow-up` / `unknown`. |
| `urgency`      | `Urgency \| None`          | `None`           | `emergency` / `high` / `medium` / `low`. |
| `tone`         | `Tone \| None`             | `None`           | `neutral` / `frustrated` / `angry` / `urgent`. |
| `history`      | `list[dict]`               | `[]`             | Conversation history (CM-32 escalation context). |
| `cost_so_far`  | `float`                    | `0.0`            | USD. Compared to `COST_CAP_USD` (5.0). |
| `search_count` | `int`                      | `0`              | Compared to `LOOP_CAP` (50). |
| `routes`       | `list[str]`                | `[]`             | Router queue; last element is the next node. |
| `output`       | `dict \| None`             | `None`           | Final reply / ticket payload set by terminal nodes. |
| `escalation`   | `EscalationRecord \| None` | `None`           | CM-32 escalation record; survives `hitl_review` (which overwrites `output`). |

### `merge(updates: dict) -> AgentState`

LangGraph applies node return values via Pydantic `model_copy(update=...)`,
which silently accepts unknown keys. `AgentState.merge` validates the
update key set explicitly and raises `ValidationError` on unknown keys,
turning silent contract drift into a loud failure.

### Enums

All four are `StrEnum` so JSON-serialization round-trips are stable.

* `Channel`: `WHATSAPP` `TELEGRAM` `EMAIL` `WEB` `UNKNOWN`
* `Intent`: `MAINTENANCE` `INQUIRY` `ESCALATION` `FOLLOW_UP` `UNKNOWN`
* `Urgency`: `EMERGENCY` `HIGH` `MEDIUM` `LOW`
* `Tone`: `NEUTRAL` `FRUSTRATED` `ANGRY` `URGENT`

---

## 3. Node contract

Every node function in `agents/orchestrator/nodes.py` follows this shape:

```python
def my_node(state: AgentState) -> dict[str, Any]:
    with langgraph_node_span("my_node", tenant_id=state.tenant_id):
        gate = guardrails.check(state)
        if gate.tripped:
            return _guardrail_termination(gate.reason)
        # ... real work here ...
        return {"intent": "...", "routes": ["next_node"]}
```

The two non-negotiables:

1. **Span wrapping** — `langgraph_node_span("<node>", ...)` from CM-21.
   Span name lands as `langgraph.node.<node>` in App Insights + LangSmith
   so backends can group cost / latency per node.
2. **Guardrail check first** — `guardrails.check(state)` as the first
   statement. If it trips, return `_guardrail_termination(reason)` and
   do no further work in this node.

### `triage` is now a real agent (CM-30)

The `triage` node is the first real specialist agent — see
[`docs/TRIAGE.md`](TRIAGE.md). It classifies `intent` / `urgency` / `tone`
with GPT-4o-mini (Pydantic-validated structured output), looks up the
tenant's recent ticket history, and routes by intent:

| Intent        | Routes to     |
|---------------|---------------|
| `maintenance` | `maintenance` |
| `inquiry`     | `knowledge`   |
| `escalation`  | `escalation`  |
| `follow-up`   | `maintenance` |
| `unknown`     | `knowledge`   |

It still runs **without** OpenAI credentials: `get_triage_classifier()`
falls back to a deterministic keyword heuristic when `OPENAI_API_KEY` is
unset, preserving the original stub's routing (`"human"`/`"escalat"` →
escalation, `"fix"`/`"broken"`/`"leak"` → maintenance, else → knowledge) so
this hello-world spine stays testable offline. The spine topology is
unchanged. CM-31 (maintenance) remains a stub.

### `escalation` is now a real agent (CM-32)

The `escalation` node is the Escalation Manager Agent — see
[`docs/ESCALATION.md`](ESCALATION.md). It sub-classifies the escalation
(`repeat`/`service_failure`/`safety`/`communication_breakdown`/`multi_issue`/`legal`),
raises a **semantic** `legal_risk` flag, persists an `EscalationRecord` to the
Cosmos `escalations` container, posts a manager alert (Slack), and prepares an
empathetic tenant draft that is **held** behind `hitl_review`. Like triage, it
runs offline (`get_escalation_classifier()` → keyword heuristic with no key).
The escalation result lives on its own `AgentState.escalation` field (the
record must survive the `hitl_review` step, which overwrites `output`).

---

## 4. HITL `interrupt()` contract

`hitl_review` calls LangGraph's `interrupt(...)` primitive. Since CM-32 the
pause payload (visible to the resumer) carries the escalation review context:

```python
{
    "reason": "escalation_review",
    "category": state.escalation.category,   # e.g. "legal"
    "legal_risk": state.escalation.legal_risk,
    "severity": state.escalation.severity,   # "high" | "critical"
    "draft": state.output.get("draft"),      # the held tenant reply
    "manager_alert": state.escalation.manager_alert,
}
```

To resume after a human has approved:

```python
from langgraph.types import Command

graph.invoke(
    Command(resume={"approved": True, "reviewer": "ops-1"}),
    config={"configurable": {"thread_id": "<same id as initial invoke>"}},
)
```

The human payload lands as `state.output["approved"]`, `state.output["via"]
== "hitl"` marks the path, and `state.output["sent"]` reflects the decision.
**Legal gate (CM-32):** the draft is marked `sent` — and the record
transitioned to `approved_sent` — **only** when `approved is True`. There is
no auto-approve path, so a legal-flagged case is never sent without a human.

---

## 5. Guardrail Stop Rules (CM-26 contract)

Defined in `agents/orchestrator/guardrails.py`. Boundaries are strict
greater-than:

| Stop rule  | Constant         | Trips when                       | Span name (CM-26 alert KQL match) |
|------------|------------------|----------------------------------|------------------------------------|
| Cost cap   | `COST_CAP_USD=5.0`| `state.cost_so_far > 5.0`        | `guardrail.cost_cap`               |
| Loop cap   | `LOOP_CAP=50`     | `state.search_count > 50`        | `guardrail.loop_cap`               |

**Dual-side regression**: the literal strings `guardrail.cost_cap` and
`guardrail.loop_cap` must match `infra/bicep/modules/alert-rules.bicep`
exactly. Two test layers protect this:

* `tests/orchestrator/test_guardrails.py::test_*_emits_exact_cm26_event_name`
* `tests/infra/test_bicep_lint.sh` greps both literals in `guardrails.py`.

Trip spans carry `request_id`, `tenant_id`, `guardrail.cost_so_far_usd`,
and `guardrail.search_count` attributes so on-call has context in the
alert payload.

---

## 6. Checkpointing

`CosmosCheckpointSaver` (sync) persists each step to the new
`checkpoints` container on the CM-17 Cosmos account:

* Partition key: `/thread_id` (one partition per graph run).
* `defaultTtl`: 30 days (auto-purge old checkpoints).
* Auth: `DefaultAzureCredential` via the CM-18 User-Assigned MI.

Selector — `get_checkpointer()` falls back to LangGraph's in-process
`MemorySaver` when `COSMOS_ENDPOINT` is unset / blank / `REPLACE-ME`
(the CM-18 placeholder pattern). Tests stay offline; demo runs without
Cosmos access.

Async variants (`aput`, `aget_tuple`, `alist`) raise `NotImplementedError` —
the CM-28 path uses the sync surface. CM-31 / CM-32 may add async if
their workloads require it.

---

## 7. Hello-world manual smoke recipe

```powershell
# 1. Install (one-time)
cd <repo root>
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# 2. Run the demo (no LLM credentials needed)
python -m agents.orchestrator.demo
```

Prints the final state + request_id. Without `APPLICATIONINSIGHTS_CONNECTION_STRING`
or `LANGCHAIN_TRACING_V2=true`, traces go to the OTel ConsoleSpanExporter
(visible on stdout).

To emit to both backends (dev environment, with KV secrets seeded):

```powershell
$env:APPLICATIONINSIGHTS_CONNECTION_STRING = "<from KV>"
$env:LANGCHAIN_TRACING_V2 = "true"
$env:LANGCHAIN_API_KEY = "<from KV>"
$env:LANGCHAIN_PROJECT = "condomanager-dev"
python -m agents.orchestrator.demo
```

The same `request_id` appears in both backends; node-level spans show as
`langgraph.node.<name>` with `tenant_id` and `request_id` attributes.

---

## 8. Maintenance Agent (`agents/maintenance/`, CM-31)

The `maintenance` node delegates to `agents.maintenance.MaintenanceAgent`. The
span + guardrail contract stays in the node; all domain logic lives in the
package. The agent is deterministic (no LLM in the hot path) so its outputs are
exactly assertable and the dedup-precision eval is reproducible in CI.

### Pipeline

```
resolve unit + category
  -> dedup query (same unit, 7-day window)
  -> OPEN duplicate?  yes -> link to original, confirm tenant, STOP (no new ticket)
                      no  -> assign priority + ETA, persist, notify manager, confirm tenant
```

### Domain model (`schema.py`)

`Ticket` (persisted to the CM-17 `tickets` container, partition key `/tenantId`):
`id` (confirmation code `TKT-XXXXXXXX`), `tenant_id`, `unit`, `issue_text`,
`category`, `priority` (`P1`–`P4`), `status` (`New` / `In Progress` / `Waiting`
/ `Resolved`), `owner`, `created_at` / `updated_at`, `request_id`,
`duplicate_of`, `eta`.

### Dedup rule (`dedup.py`, AC2)

A candidate duplicates an existing ticket iff: same **resolved** unit (an
`unknown` unit never matches — protects precision), same coarse `categorize`
bucket, token-Jaccard `similarity >= 0.3`, and the existing ticket is within
`DEDUP_WINDOW_DAYS = 7`. `find_open_duplicate` additionally skips `Resolved`
tickets (a recurrence opens a fresh ticket and bumps priority via `is_repeat`).
`is_duplicate_pair` is the predicate the AC6 precision eval grades.

### Priority (`priority.py`, AC3)

Base band from `Urgency` (`None` -> `MEDIUM`, since CM-30 Triage isn't merged
yet). `ANGRY`/`URGENT` tone bumps one band; repeat-status bumps one band; bumps
clamp at `P1`. ETA is keyed off the band (`P1` "within 2 hours" … `P4` "within
5 business days").

### Seams (env-gated, mirror `get_checkpointer`)

* `get_ticket_repository()` — `CosmosTicketRepository` when `COSMOS_ENDPOINT` is
  a real value, else a cached `InMemoryTicketRepository` (offline tests + demo).
* `get_notifier()` — `LoggingNotifier` (PII-masked) by default. **Real Slack/
  email transport is CM-32's shared deliverable**; CM-31 composes the manager
  alert and dispatches it through this seam.

### Node outputs

`state.output["status"]` is `ticket_created` (new) or `duplicate` (linked),
carrying `ticket_id`, `unit`, `category`, `priority`, `eta`, and a tenant
`confirmation` string. A tripped guardrail still short-circuits to
`guardrail_terminated` before any repository write.

---

## 9. Knowledge Agent — RAG over Cosmos (CM-33)

The `knowledge` node answers tenant policy questions by RAG over the Cosmos
`policies-vector` container that the CM-34 gdrive-sync job populates. It reuses
CM-34's `agents.knowledge` write-side primitives (`chunk_text`,
`default_embedder`) and adds the read side.

**Flow** (`agents/knowledge/`):

1. **Retrieve** (`retrieval.retrieve`) — embed the question, run a vector
   search (`CosmosVectorStore.search_chunks` → `VectorDistance()`) + a keyword
   search (`keyword_search` → `CONTAINS`), and fuse the two rank lists with
   **Reciprocal Rank Fusion** into the top-k `RetrievedChunk`s.
2. **Answer** (`rag.answer_question`) — a strict "use ONLY the numbered context
   passages" prompt yields a `KnowledgeAnswer` with inline `[n]` citations.
   Confidence = the top chunk's cosine similarity, used **directly** from
   `VectorDistance()` (`1.0` = identical), clamped to `[0, 1]`. **(CM-47:** the
   earlier `1 - VectorDistance` arithmetic *inverted* the score — a perfect match
   scored `0` and got refused — and was removed. `VectorDistance` already returns
   a cosine *similarity*, not a distance. See [`docs/INFRA.md`](INFRA.md) §Cosmos.**)**
3. **Refuse + hand off** — if confidence `< CONFIDENCE_THRESHOLD` (0.6) or
   nothing grounds the answer, the node sets `refused=True` and appends
   `"maintenance"` to `state.routes`; `graph._knowledge_router` then hands the
   request to the Maintenance agent.

**Model seam** (`llm.get_chat_model`): GPT-4o-mini `LLMChatModel` when
`OPENAI_API_KEY` is set (sourced from Key Vault), else a deterministic
`StubChatModel` — same env-driven pattern as CM-30's `get_triage_classifier`,
so tests/CI run offline. When `COSMOS_ENDPOINT` is unset the node refuses
without any model/network call, preserving the no-credentials contract.

**Hallucination control:** the model must answer only from the retrieved
passages and set `can_answer=false` otherwise; citations are validated against
the retrieved set before they reach `output`.

**Eval:** `tests/eval/knowledge_seed.jsonl` + `tests/knowledge/test_eval_knowledge.py`
assert >25% self-service resolution and <1% hallucination using the stub model
(reproducible in CI); the real-LLM eval is opt-in behind `OPENAI_API_KEY`.

---

## 10. Vendor Agent (`agents/vendor/`, CM-35)

The `vendor` node runs after `maintenance` and delegates to
`agents.vendor.VendorAgent`. It reads the created ticket from `state.output`
(`category` / `priority` / `unit` / `ticket_id`), matches a contractor, and
either auto-dispatches or routes to `hitl_review` for manager approval. All
logic is deterministic (no LLM); seams follow the `get_checkpointer()` /
`get_ticket_repository()` pattern, so the suite runs offline.

### Pipeline

```
non-`ticket_created` output (duplicate / guardrail) -> pass through to END
match vendors (category + availability + performance) -> none -> alert manager, END
  -> decide (AC3): pre_approved AND est_cost < threshold AND not safety/legal
       auto-dispatch:  notify vendor (email/SMS seam), END
       needs approval: alert manager, route to hitl_review
```

### Matching (`matching.py`, AC2)

Filter by category + availability (weekday calendar), rank by
`performance_score` (desc), tie-break cheaper `cost_tier`, then id. Pre-approval
is **not** a matching filter — it feeds the dispatch decision, so the best
contractor is surfaced even when it needs approval.

### Dispatch rule (`dispatch.py`, AC3)

Auto-dispatch only if **all**: vendor `pre_approved`, `estimate_cost(category,
priority) < threshold` (default $250), and **not** safety/legal. Safety/legal
is derived conservatively — `P1`/emergency, `structural` category, or
`intent == escalation` (CM-31 tickets carry no explicit legal flag; CM-32 owns
the semantic classifier) — erring toward requiring approval.

### Seams + boundaries

* `get_vendor_repository()` — seeded in-memory roster (`seed.py`). **No Cosmos
  `vendors` container yet** — a deferred infra follow-up.
* `get_vendor_notifier()` — `LoggingVendorNotifier` (PII-masked) by default;
  real email/SMS (Twilio) deferred. The **manager** approval alert reuses
  CM-31's `agents.maintenance.get_notifier`.
* Real Slack/email approval transport is **CM-32's** shared work. CM-35
  composes the approve/deny payload and gates via `hitl_review`.

### Node outputs

The vendor node adds `vendor_status` to `state.output`: `auto_dispatched`
(+ `vendor_id`, `estimated_cost`), `pending_approval` (+ `vendor_id`,
`approval_reason`, `approval_requested_at`), or `no_vendor`. It always sets
`routes` to `vendor_done` (-> END) or `hitl_review` (manager approval). The
`<15 min` approval round-trip (AC5) is an operational SLA (CM-26 alerts), not
agent code — the agent stamps `approval_requested_at`. Post-approval dispatch
finalization (resume → notify vendor) is a documented follow-up.
