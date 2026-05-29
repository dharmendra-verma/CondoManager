# LangGraph Spine — `agents/orchestrator/`

> Jira: **CM-28** | Epic: CM-Epic 4 (LangGraph Orchestrator) | Phase 0

This is the orchestrator: a `StateGraph(AgentState)` with nodes for triage,
knowledge, maintenance, escalation, HITL review, and a guardrail-terminated
terminal. CM-30 / CM-31 / CM-32 replace the stub bodies one at a time without
touching the spine. **`maintenance` is now real (CM-31)** — see §8; `triage`,
`knowledge`, and `escalation` remain stubs until their stories land.

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
   |          |          v               |
   |          |     hitl_review          |
   |          |          |               |
   +----------+----------+---------------+
                         |
                         v
                        END
```

* **Entry**: `START -> triage` (unconditional).
* **Router**: `agents.orchestrator.graph._router` reads `state.routes[-1]`
  and dispatches. Default is `"triage"` when `routes` is empty.
* **Stop short-circuit**: Any node whose guardrail trips returns
  `routes=["guardrail_terminated"]` and skips its real work.
* **HITL**: `escalation -> hitl_review -> END`. `hitl_review` calls
  LangGraph's `interrupt(...)` primitive; the graph pauses and resumes
  via `graph.invoke(Command(resume=<payload>), config=...)`.

---

## 2. `AgentState` reference

Pydantic `BaseModel` defined in `agents/orchestrator/state.py`. **13 fields**,
exactly per the CM-28 AC.

| Field          | Type                | Default          | Notes                                |
|----------------|---------------------|------------------|--------------------------------------|
| `tenant_id`    | `str`               | _required_       | Per-tenant scoping for all telemetry. |
| `request_id`   | `str`               | _required_       | Correlates with CM-21 `request_id` ContextVar. |
| `channel`      | `Channel`           | `UNKNOWN`        | `whatsapp` / `telegram` / `email` / `web` / `unknown`. |
| `raw_message`  | `str`               | `""`             | Inbound tenant message — set at the entry adapter. |
| `normalized`   | `dict`              | `{}`             | Channel-normalized payload (CM-29). |
| `intent`       | `Intent \| None`    | `None`           | `maintenance` / `inquiry` / `escalation` / `follow-up` / `unknown`. |
| `urgency`      | `Urgency \| None`   | `None`           | `emergency` / `high` / `medium` / `low`. |
| `tone`         | `Tone \| None`      | `None`           | `neutral` / `frustrated` / `angry` / `urgent`. |
| `history`      | `list[dict]`        | `[]`             | Conversation history (CM-32 escalation context). |
| `cost_so_far`  | `float`             | `0.0`            | USD. Compared to `COST_CAP_USD` (5.0). |
| `search_count` | `int`               | `0`              | Compared to `LOOP_CAP` (50). |
| `routes`       | `list[str]`         | `[]`             | Router queue; last element is the next node. |
| `output`       | `dict \| None`      | `None`           | Final reply / ticket payload set by terminal nodes. |

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

The stub `triage` node uses a tiny keyword heuristic over `raw_message`
to make the spine fully testable without an LLM:

| Message contains              | Routes to    |
|-------------------------------|--------------|
| `"human"` or `"escalat"`      | `escalation` |
| `"fix"` / `"broken"` / `"leak"`| `maintenance` |
| anything else                 | `knowledge`  |

CM-30 replaces this with real GPT-4o-mini classification; the spine
stays the same.

---

## 4. HITL `interrupt()` contract

`hitl_review` calls LangGraph's `interrupt(...)` primitive. The pause
payload (visible to the resumer) is:

```python
{"reason": "stub-hitl-review", "draft": state.output.get("draft")}
```

To resume after a human has approved:

```python
from langgraph.types import Command

graph.invoke(
    Command(resume={"approved": True, "reviewer": "ops-1"}),
    config={"configurable": {"thread_id": "<same id as initial invoke>"}},
)
```

The whatever-the-human-sent payload lands as
`state.output["approved"]`, and `state.output["via"] == "hitl"` marks
the path. CM-32 will replace `_guardrail_termination`'s minimal escalation
draft with a real tenant-facing reply behind the same gate.

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
