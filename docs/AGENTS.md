# LangGraph Spine — `agents/orchestrator/`

> Jira: **CM-28** | Epic: CM-Epic 4 (LangGraph Orchestrator) | Phase 0

This is the minimal viable orchestrator: a `StateGraph(AgentState)` with
stub nodes for triage, knowledge, maintenance, escalation, HITL review,
and a guardrail-terminated terminal. CM-30 / CM-31 / CM-32 will replace
the stub bodies one at a time without touching the spine.

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
unchanged. CM-31 (maintenance) and CM-32 (escalation) remain stubs.

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

## 8. Knowledge Agent — RAG over Cosmos (CM-33)

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
   Confidence = the top chunk's cosine similarity (`1 - VectorDistance`).
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
