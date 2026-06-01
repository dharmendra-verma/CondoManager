# Observability (CM-21)

How CondoManager's Python code emits OpenTelemetry traces, how `request_id`
is propagated, and how to wire LLM spans without double-emission between
openinference and Traceloop.

The OpenTelemetry **backend** (Application Insights) lands in CM-22 — for
CM-21, leave `OTEL_EXPORTER_OTLP_ENDPOINT` unset and spans go to the
ConsoleSpanExporter so you can read them on stdout.

## Quickstart

```python
from agents.observability import configure_otel, with_request_id, langgraph_node_span

# At app startup (once per process):
configure_otel(service_name="orchestrator", environment="dev", app=fastapi_app)

# At every inbound boundary (HTTP handler, queue consumer, scheduled job):
with with_request_id() as request_id:
    ...  # all work in this block carries the request_id

# Around a LangGraph node body (CM-28 onward):
with langgraph_node_span("triage", tenant_id=state.tenant_id, model="gpt-4o-mini"):
    classification = await classify(state.message)
```

## Environment variables

| Var | Default | Effect |
|---|---|---|
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | _(unset)_ | **CM-22.** When set to a real value, delegates to `azure-monitor-opentelemetry` (Azure Monitor exporter + Live Metrics + sampler). Takes precedence over `OTEL_TRACES_EXPORTER`. The CM-18 placeholder `REPLACE-ME` is treated as if-unset so the Container App boots before the post-deploy seed step. |
| `OTEL_SAMPLER_RATIO` | `1.0` | **CM-22.** Client-side sampling ratio (Parent-based). `1.0` -> ParentBased(AlwaysOn); fractional values -> ParentBased(TraceIdRatioBased). Clamped to [0,1] with a warning on out-of-range. App Insights applies a second, server-side adaptive sampler on top (`SamplingPercentage` on the Bicep resource). |
| `OTEL_TRACES_EXPORTER` | `console` | `console` -> ConsoleSpanExporter; `otlp` -> OTLP-HTTP exporter (requires endpoint, see below); any other value -> no-op. **Ignored when `APPLICATIONINSIGHTS_CONNECTION_STRING` is set.** |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | _(unset)_ | OTLP-HTTP target for non-App-Insights backends. When unset, `OTEL_TRACES_EXPORTER=otlp` falls back to ConsoleSpanExporter rather than crashing. |
| `OTEL_EXPORTER_OTLP_HEADERS` | _(unset)_ | OTLP exporter headers (e.g. `Authorization=Bearer …`). Read by `OTLPSpanExporter` automatically. |
| `OTEL_SERVICE_VERSION` | `0.1.0` | Goes into the `service.version` resource attribute. |
| `OTEL_SERVICE_NAME` | _(set in code)_ | Overrides the `service_name` argument to `configure_otel`. Useful when the same image runs in multiple roles. |

### Exporter precedence
1. `APPLICATIONINSIGHTS_CONNECTION_STRING` set (and not `REPLACE-ME`) -> Azure Monitor distro
2. `OTEL_TRACES_EXPORTER=otlp` AND `OTEL_EXPORTER_OTLP_ENDPOINT` set -> OTLP-HTTP
3. Otherwise -> ConsoleSpanExporter (default; what tests + local dev use)

`configure_otel(service_name=..., environment=...)` is idempotent — call
it again on hot-reload, in tests, or from a serverless cold-start without
worrying about double-init.

## `request_id` flow

`request_id` is the single value that joins:

* OTel **spans** (attribute `request_id`)
* OTel **baggage** (propagated to downstream services via HTTP headers)
* Structured **log lines** (read from the ContextVar by the formatter
  that lands with CM-27)

The ContextVar handles in-process async — including `asyncio.gather` and
FastAPI's per-request task isolation — so two concurrent requests never
see each other's id.

```python
from agents.observability import with_request_id, get_request_id, new_request_id

# Inbound boundary — pass the client-supplied id if present, else mint one:
async def handle(req: Request):
    client_id = req.headers.get("X-Request-Id")
    with with_request_id(client_id) as rid:
        # rid is set in: ContextVar, OTel baggage, active span attribute.
        ...
        outgoing_response.headers["X-Request-Id"] = rid

# Elsewhere in the code:
log.info("doing thing", extra={"request_id": get_request_id()})
```

Outside any `with_request_id(...)` scope, `get_request_id()` returns the
sentinel `"unknown"` — never raises. Spans emitted in that situation are
still well-formed (the `request_id` attribute is just `"unknown"`).

## Why openinference + Traceloop both?

The story names both explicitly. They overlap on LangChain/openai
auto-instrumentation, so we use them with **disjoint scopes**:

| Library | Scope | Why |
|---|---|---|
| `openinference-instrumentation-openai` | Raw `openai` SDK calls | Richer LLM-specific attributes than Traceloop's openai instrumentor (matches the shape Phoenix / Arize / LangSmith expect). |
| `traceloop-sdk` | LangChain + LlamaIndex | High-level chain tracing — wraps `LCEL` / `LangGraph` execution. We initialise it with `instruments={LANGCHAIN, LLAMA_INDEX}` so its built-in openai instrumentor is NOT installed and we don't double-emit. |
| `opentelemetry-instrumentation-fastapi` | FastAPI HTTP handlers | Standard. |
| `opentelemetry-instrumentation-httpx` | httpx clients | Standard. |

The disjointness is asserted in
`tests/observability/test_instrumentation.py::test_openai_span_is_owned_by_openinference`
— it mocks the OpenAI endpoint with `respx` and verifies the produced
span's `instrumentation_scope.name` contains `openinference`.

### Troubleshooting: double-emitted OpenAI spans

If you see two spans per OpenAI call (one from `openinference.openai` and
one from `traceloop.openai`), the Traceloop `instruments` allowlist isn't
being honoured by the installed Traceloop version. Workarounds:

1. **Pin Traceloop forward** to a version where the `instruments` kwarg
   carves openai out cleanly. Update `requirements-lock.txt`.
2. **Swap order**: in `agents/observability/instrumentation.py`, call
   `Traceloop.init(...)` first, then call
   `OpenAIInstrumentor().instrument()` (openinference) second. The last
   writer wins; openinference replaces Traceloop's wrapper.
3. **Uninstrument explicitly**: import
   `from traceloop.sdk.instruments import OpenAI as TraceloopOpenAI`
   and call its `.uninstrument()` after `Traceloop.init`.

## Manual span helpers

Auto-instrumentation covers FastAPI / httpx / openai / LangChain. It does
NOT cover bare LangGraph node functions — those need to open a span
themselves. Use `langgraph_node_span`:

```python
from agents.observability import langgraph_node_span

async def triage_node(state: AgentState) -> AgentState:
    with langgraph_node_span("triage", tenant_id=state.tenant_id) as span:
        result = await classify(state.message)
        span.set_attribute("intent", result.intent)
        return state.update(intent=result.intent)
```

Conventions:

* Span name is `langgraph.node.<node_name>` so backends can group spans
  by node.
* The current `request_id` is set as an attribute automatically.
* Extra kwargs become span attributes; `None` values are dropped so you
  can pass optionals without an `if`.

## Local demo

```bash
OTEL_TRACES_EXPORTER=console python -m agents.observability.demo
# In another terminal:
curl http://localhost:8000/echo/hello
```

You'll see spans printed for the FastAPI handler, the outbound httpx
call, and the openai call. Useful as a sanity check before wiring
Application Insights (CM-22).

## Application Insights backend (CM-22)

Workspace-based App Insights (`appi-condomanager-<env>`, linked to the
existing LAW from CM-16) is the OTLP backend. The connection string is
held in Key Vault as `app-insights-connection-string` (CM-18 schema) and
mounted into the Container App as `APPLICATIONINSIGHTS_CONNECTION_STRING`
via native `secretRef` resolved through the User-Assigned MI. Once that
env var has a real value, `configure_otel` flips into the Azure Monitor
branch automatically.

### Post-deploy one-time setup

```bash
# 1. Populate the KV secret from the deployment output (idempotent):
bash infra/scripts/seed-app-insights-secret.sh dev

# 2. Force a new revision so the Container App picks up the seeded value:
az containerapp update --name ca-hello-condomanager-dev --resource-group rg-condomanager

# 3. Hit the app; spans should appear in App Insights UI within ~30s.
```

The seed script reads `properties.outputs.appInsightsConnectionString.value`
from the latest deployment and writes it into KV. It refuses to overwrite
a real value that differs from the deployment output — operator rotations
are preserved.

### Layered sampling (AC #3)

* **SDK-side (Python).** `OTEL_SAMPLER_RATIO` (default 1.0) becomes a
  `ParentBased` sampler — `ParentBased(AlwaysOn)` at 1.0, otherwise
  `ParentBased(TraceIdRatioBased(r))`. Dev keeps 1.0; prod is typically
  0.5 — set per env by the operator (Container App `env` or `secretRef`).
* **Server-side (App Insights).** The `SamplingPercentage` property on
  the Bicep resource (`appInsightsSamplingPercentage` param on `main.bicep`,
  default 100 dev / 50 prod) drives App Insights' adaptive sampler at
  ingestion. Halves prod cost without changing client behavior.

Live Metrics is **unsampled** and rides above both samplers — operators
get a real-time view of cost / latency / errors even when traces are
heavily sampled.

### Troubleshooting

* **No spans in App Insights.** Verify the conn string was seeded:
  `az keyvault secret show --vault-name kv-condomanager-dev --name app-insights-connection-string`.
  If it's still `REPLACE-ME`, run `seed-app-insights-secret.sh dev`.
* **`configure_azure_monitor` API change.** Pin in `requirements-lock.txt`
  protects CI; if a bump breaks the `enable_live_metrics` or `sampler`
  kwarg, fall back to constructing `AzureMonitorTraceExporter` + Live
  Metrics manually (see `azure.monitor.opentelemetry.exporter`).
* **Server-side sampling masks issues.** Temporarily flip
  `SamplingPercentage` to 100 by redeploying with
  `--parameters appInsightsSamplingPercentage=100`. Live Metrics stays
  unsampled regardless.

## LangSmith (dev only) — CM-23

LangSmith is the **developer-facing** trace backend for prompt iteration
and offline evals. Enabled by default on dev (Bicep param
`langsmithEnabled` defaults to `env == 'dev'`); off on prod by default
because CM-24 will wire Langfuse for prod LLM observations and running
both would double-pay per call.

| Env var | Set by | Effect |
|---|---|---|
| `LANGCHAIN_TRACING_V2` | Container App env block (`'true'`) | LangChain's native callback ships traces to LangSmith |
| `LANGCHAIN_API_KEY` | KV `secretRef` → `langsmith-api-key` | API key minted in the LangSmith UI |
| `LANGCHAIN_PROJECT` | `condomanager-<env>` (set in main.bicep) | Project routing in the LangSmith UI |
| `LANGCHAIN_ENDPOINT` | Bicep param, default `https://api.smith.langchain.com` | US default; override to `eu.api.smith.langchain.com` for EU |

### Dual emission with App Insights — by design

In dev, the same LangChain call emits to **both** backends:

| Pipeline | Layer hooked | Audience |
|---|---|---|
| Traceloop (CM-21) -> OTel -> App Insights (CM-22) | LangChain `Runnable` instrumentation | Ops view — latency / error / cost in App Insights workbooks (CM-25) |
| LangChain native callback (CM-23) -> LangSmith | LangChain `BaseCallbackHandler` | Dev view — prompt iteration, trace replay, offline evals |

The two pipelines hook **different** layers of LangChain — they don't fight
over span ownership. Tested for non-interference in
`tests/observability/test_langsmith.py::test_appi_branch_wins_when_langsmith_env_also_set`.

**Cost note.** LangSmith Hobby includes 5K traces/mo; expect to stay well
under in dev. Prod is intentionally off so the team doesn't double-pay
for LLM observability — Langfuse owns prod (CM-24). If LangSmith
ingestion ever becomes painful in dev, knob is the SDK env var
`LANGCHAIN_TRACING_SAMPLING_RATE` (0.0-1.0).

### Post-deploy one-time setup

```bash
# 1. Mint a LangSmith API key + create the project `condomanager-dev` in
#    the LangSmith UI (auto-creates on first trace; explicit creation lets
#    you set retention + sharing first).
# 2. Populate the KV secret:
az keyvault secret set --vault-name kv-condomanager-dev \
    --name langsmith-api-key --value "<langsmith-key>"
# 3. Force a Container App revision so it picks up the seeded secret:
az containerapp update --name ca-hello-condomanager-dev --resource-group rg-condomanager
# 4. Seed the eval dataset stub from tests/eval/triage_seed.jsonl:
LANGCHAIN_API_KEY="<key>" \
    python infra/scripts/seed-langsmith-dataset.py --env dev
```

Re-runs of the seed script are idempotent: it skips example uploads whose
`inputs` fingerprint already exists in the dataset, and skips dataset
creation if the dataset already exists.

### Manual smoke test (AC #3)

```bash
LANGCHAIN_TRACING_V2=true \
LANGCHAIN_API_KEY=<key> \
LANGCHAIN_PROJECT=condomanager-dev \
OPENAI_API_KEY=<openai-key> \
python -m agents.observability.langchain_demo --message "kitchen sink is leaking"
```

The trace should appear in the LangSmith UI under `condomanager-dev`
within ~30s. If env vars are missing or hold `REPLACE-ME`, the demo
exits 1 with a clear notice — it never silently runs without producing a
trace.

### Eval dataset workflow (CM-30 prep)

`tests/eval/triage_seed.jsonl` carries 10 stub examples covering the four
target Triage intents (maintenance / inquiry / escalation / follow-up).
CM-30 expands this to 200. The on-disk fixture is the source of truth:

```bash
# Edit the JSONL, then re-run the seed script.
# Already-uploaded examples are skipped (fingerprint = JSON of `inputs`).
python infra/scripts/seed-langsmith-dataset.py --env dev
```

## Operations workbook (CM-25)

Azure Workbook `CondoManager Ops — <env>` lives under
**Azure Portal → `appi-condomanager-<env>` → Workbooks → My workbooks**.
Backed by the CM-22 App Insights component (workspace-based, queries land
in the CM-16 Log Analytics workspace under the hood). Provisioned by
`infra/bicep/modules/workbook.bicep`; the serialized payload (5 sections:
header + time-range parameter + 4 KQL panels) is hand-written and lives
beside the Bicep at `workbook-payload.json`.

**Most panels will be empty until CM-28 (LangGraph spine) and CM-30
(Triage Agent) ship live LLM traffic.** The workbook is built now to
lock the OTel attribute schema and the `hitl.queued` / `hitl.resolved`
custom-events contract; future stories drop spans into these panels
without changing the workbook.

### Panels

| Panel | What it shows | Source rows / events |
|---|---|---|
| **Cost per day (USD)** | Daily LLM spend modeled from token counts × model-rate table | `dependencies` where `gen_ai.system == 'openai'` or `openinference.span.kind == 'LLM'` |
| **Latency p50 / p95 / p99** | Per-operation latency percentiles, 5-minute buckets | `requests` (FastAPI handlers — CM-21 OTel auto-instrumentation) |
| **Error rate per LangGraph node** | Per-node error percentage, hourly | `dependencies` where `customDimensions['langgraph.node']` is set |
| **HITL queue depth** | Cumulative `hitl.queued` − `hitl.resolved`, 5-minute buckets | `customEvents` named `hitl.queued` / `hitl.resolved` (contract — see below) |

### KQL queries — canonical source

The queries in `infra/bicep/modules/workbook-payload.json` are the **deployed**
source of truth; the same KQL is mirrored here so reviewers don't have to
unpack the JSON.

#### Cost per day

```kql
// Coalesces openinference and OTel gen_ai semantic conventions for token
// + model. Pricing table — update when adopting a new model.
let model_rates = datatable(model: string, prompt_per_token: real, completion_per_token: real) [
    'gpt-4o-mini',  0.00000015, 0.00000060,
    'gpt-4o',       0.00000250, 0.00001000,
    'gpt-4-turbo',  0.00001000, 0.00003000
];
dependencies
| where customDimensions['gen_ai.system'] == 'openai'
    or customDimensions['openinference.span.kind'] == 'LLM'
| extend
    model = coalesce(
        tostring(customDimensions['gen_ai.request.model']),
        tostring(customDimensions['llm.model_name'])
    ),
    prompt_tokens = toint(coalesce(
        customDimensions['gen_ai.usage.prompt_tokens'],
        customDimensions['llm.token_count.prompt']
    )),
    completion_tokens = toint(coalesce(
        customDimensions['gen_ai.usage.completion_tokens'],
        customDimensions['llm.token_count.completion']
    ))
| join kind=leftouter (model_rates) on model
| extend cost_usd =
    (prompt_tokens * coalesce(prompt_per_token, 0.0))
    + (completion_tokens * coalesce(completion_per_token, 0.0))
| summarize daily_cost_usd = sum(cost_usd) by bin(timestamp, 1d)
| order by timestamp asc
```

#### Latency p50 / p95 / p99

```kql
requests
| where success in (true, false)
| summarize
    p50_ms = percentile(duration, 50),
    p95_ms = percentile(duration, 95),
    p99_ms = percentile(duration, 99)
  by bin(timestamp, 5m), operation_Name
| order by timestamp desc
```

#### Error rate per LangGraph node

```kql
dependencies
| where isnotempty(tostring(customDimensions['langgraph.node']))
| extend agent = tostring(customDimensions['langgraph.node'])
| summarize
    total = count(),
    errors = countif(success == false)
  by bin(timestamp, 1h), agent
| extend error_rate_pct = todouble(errors) / total * 100.0
| project timestamp, agent, error_rate_pct
| order by timestamp desc
```

#### HITL queue depth

```kql
customEvents
| where name in ('hitl.queued', 'hitl.resolved')
| summarize
    queued = countif(name == 'hitl.queued'),
    resolved = countif(name == 'hitl.resolved')
  by bin(timestamp, 5m)
| order by timestamp asc
| extend running_queued = row_cumsum(queued)
| extend running_resolved = row_cumsum(resolved)
| extend queue_depth = running_queued - running_resolved
| project timestamp, queue_depth
```

### HITL events contract

The HITL queue panel reads `customEvents`. The future HITL story emits:

```python
from opentelemetry import trace
tracer = trace.get_tracer(__name__)

# When a task is pushed to a human reviewer:
with tracer.start_as_current_span("hitl.queued") as span:
    span.set_attribute("hitl.task_id", task_id)
    span.set_attribute("hitl.reason", reason)

# When the reviewer signs off:
with tracer.start_as_current_span("hitl.resolved") as span:
    span.set_attribute("hitl.task_id", task_id)
    span.set_attribute("hitl.outcome", outcome)
```

Stick to those literal event names so the workbook displays without changes.

### One-time pin to dashboard (operator step — AC #3)

Programmatic dashboard pinning is intentionally out of scope:
`Microsoft.Portal/dashboards` would pre-empt each operator's per-user
dashboard customization and the dashboard JSON breaks when Azure renames
the workbook part. The pin step is operator-driven:

1. Open the workbook in the Azure Portal (App Insights → Workbooks → "CondoManager Ops — dev").
2. Click **Edit**, then **Done editing** to enter the workbook view.
3. From the workbook header click **Pin** → **Pin all** (or select specific panels).
4. Pick the dashboard. Create a new one named `condomanager-ops-<env>` the first time.

### Why model-rate KQL, not Azure Cost Management?

* Cost Management's REST API surfaces *billed* spend with hours of latency
  and doesn't attribute per-request / per-agent. The workbook needs
  per-span granularity so the latency / errors / cost panels share the
  same span universe.
* The model-rate `datatable` is a 10-line block that updates rarely.
  Real-billing reconciliation lands in CM-26 (budget alerts) — that
  story can layer Cost Management on top of the same span scope.

### Why coalesce openinference + OTel `gen_ai.*`?

CM-21's `openinference-instrumentation-openai` emits
`llm.token_count.prompt` / `llm.token_count.completion` /
`llm.model_name`. The OTel semantic conventions for `gen_ai.*` are
stabilising and will gradually replace these. The KQL `coalesce(...)`
keeps the workbook working through that transition — neither side
needs to ship before the other.

## Structured logging (CM-27)

Every Python log line is a single JSON object on stdout. Container Apps
captures stdout to the Log Analytics workspace (CM-16 + CM-22), where KQL
queries can join logs to App Insights spans and Langfuse observations by
`request_id` — the single correlation primitive shared across all three.

Why stdout, not the Azure Monitor logger? CM-22's
`configure_azure_monitor(logger_name=None)` explicitly deferred Python
logging to this story. Container Apps already pipes stdout to Log
Analytics, so there's no missing channel.

### Quickstart

```python
from agents.observability import configure_logging, with_request_id, with_tenant

configure_logging(service_name="orchestrator", environment="dev")

# Inbound boundary (HTTP handler, queue consumer, scheduled job)
with with_request_id() as rid, with_tenant("tenant-42"):
    import logging
    logging.getLogger(__name__).info("processing message")
# stdout:
# {"ts": "2026-05-29 ...", "level": "INFO", "logger": "agents.orchestrator",
#  "msg": "processing message", "service_name": "orchestrator",
#  "environment": "dev", "service_version": "0.1.0",
#  "request_id": "req_a1b2c3d4e5f6", "tenant_id": "tenant-42"}
```

`configure_logging()` is idempotent — call it once at app startup; subsequent
calls (e.g. from notebook reloads) are no-ops.

### Schema

| Field | Type | Source | Always present? |
|------|------|--------|-----------------|
| `ts` | string | record timestamp | yes |
| `level` | string | `DEBUG` / `INFO` / `WARNING` / `ERROR` | yes |
| `logger` | string | logger name | yes |
| `msg` | string | rendered message, PII-masked | yes |
| `service_name` | string | `configure_logging(service_name=…)` | yes |
| `environment` | string | `dev` / `prod` | yes |
| `service_version` | string | `OTEL_SERVICE_VERSION` env, default `0.1.0` | yes |
| `request_id` | string | `with_request_id()` contextvar (CM-21) | yes (may be `"unknown"`) |
| `tenant_id` | string | `with_tenant()` contextvar (CM-27) | only when scoped |
| `agent_name` | string | `with_agent()` contextvar (CM-27) | only when scoped |
| `exc_info` | string | when `logger.exception(...)` | only when present |
| `stack_info` | string | when `stack_info=True` is passed | only when present |

`request_id: "unknown"` is a real signal — some code path skipped
`with_request_id()` at the inbound boundary; the field is intentionally
emitted (not omitted) so KQL can search for those gaps.

### PII masking (starter set)

Applied via a `logging.Filter` BEFORE the JSON formatter, so masked text
is what KQL ingests. **Not exhaustive** — supplement per-vendor as the
data flows become real.

| Pattern | Example input | Example output |
|---------|---------------|----------------|
| Email | `user@example.com` | `***@***.***` |
| Phone (E.164 only) | `+919876543210` | `+***` |
| Credit card (Luhn-pass) | `4111-1111-1111-1111` | `****-****-****-1111` |
| API keys (`sk-…`, `pk_…`, `AKIA…`, `ASIA…`) | `sk-abcdef1234567890ABCDEF` | `***REDACTED-KEY***` |

Known limitations:

* Non-E.164 phone formats (`(555) 123-4567`, `+1 555-…`) are NOT matched.
* Stack traces in `exc_info` may contain file paths or `repr()` of objects
  with PII — the filter doesn't walk the rendered exception text.
* Names, addresses, and other free-text PII are out of scope.

If a category becomes a real concern, add a pattern to
`agents/observability/pii.py` with a test in `test_pii.py`.

### Joining logs to spans and Langfuse observations

`request_id` is the single pivot across:

* **Log lines** — emitted by this module on every record.
* **OTel spans** — CM-21 sets `request_id` as a span attribute inside
  `with_request_id()`.
* **Langfuse observations** — CM-24's `observe_node` decorator binds
  `request_id` as observation metadata.

So a three-way join in KQL works the same way regardless of which surface
the operator started in:

```kusto
// Find every log line for one tenant message
AppTraces
| where Properties.request_id == "req_a1b2c3d4e5f6"
| project TimeGenerated, Message, Properties
| order by TimeGenerated asc

// Same request_id, on the spans side
AppDependencies
| where customDimensions.request_id == "req_a1b2c3d4e5f6"
| order by timestamp asc
```

## Alerts (CM-26)

One shared Action Group (`ag-condomanager-<env>`) fanned out to four
alerts: a `Microsoft.Consumption/budgets` resource with 50/80/100%
thresholds, and three `Microsoft.Insights/scheduledQueryRules` over
the CM-22 App Insights component. Same KQL family as the CM-25
workbook — alerts are "send a page when the workbook would turn red".

### Alerts at a glance

| Alert | Severity | Frequency | Window | Threshold | Source |
|---|---|---|---|---|---|
| `budget-condomanager-<env>` | (Azure Cost Mgmt) | continuous | monthly | 50/80/100% of `alertMonthlyBudgetUsd` | RG-scoped Consumption Budget |
| `alert-latency-slo-<env>` | 2 (BH page) | every 5m | 5m | p95 > 2000ms | `requests` percentile() |
| `alert-guardrail-trip-<env>` | **1** (page now) | every 5m | 5m | any `guardrail.*` event | `customEvents` |
| `alert-hallucination-spike-<env>` | 2 (BH page) | every 15m | 1h | refusal_pct < 1% AND total > N | `dependencies` × `customEvents` |

Pre-CM-28 the three query-based alerts evaluate to zero rows and don't
fire — same posture as the CM-25 workbook panels. The budget alerts
might fire if RG spend reaches a threshold, but phase 0 spend is
near-zero too.

### Action Group — Slack + email

`ag-condomanager-<env>` (Microsoft.Insights/actionGroups, global). Two
optional receivers:

| Receiver | Param | When omitted | Notes |
|---|---|---|---|
| Email | `alertEmail` | Email receiver list is empty | Plain string — email is not a credential |
| Slack webhook | `alertSlackWebhookUrl` (`@secure()`) | Webhook receiver list is empty | URL is the auth token; never commit to `main.parameters.json` |

`useCommonAlertSchema: true` is set on both receivers so the JSON
shape Azure sends is consistent across all alert types — operators
write one Slack message formatter and reuse it.

### Operator one-time setup (post-deploy)

```bash
# Option A — supply via --parameters on deploy (preferred):
az deployment group create \
  --resource-group rg-condomanager \
  --template-file infra/bicep/main.bicep \
  --parameters infra/bicep/main.parameters.json \
  --parameters env=dev \
              alertSlackWebhookUrl='https://hooks.slack.com/services/T.../B.../X...' \
              alertEmail='ops@example.com'

# Option B — post-deploy via the Azure Portal:
# Monitor → Alerts → Action Groups → ag-condomanager-dev → Notifications → add
```

**Antipattern:** putting `alertSlackWebhookUrl` into
`infra/bicep/main.parameters.json`. Even though Azure encrypts secure
params in deployment history, the file is in git. Use `--parameters`
or the Portal.

### Canonical KQL — the three scheduled-query rules

#### Latency SLO (severity 2)

```kql
requests
| where timestamp > ago(5m)
| summarize p95_ms = percentile(duration, 95)
| where p95_ms > 2000
```

Single-window p95 > 2000ms over 5m, evaluated every 5m. Phase 0
simplification — the formal multi-window burn-rate alert (Google SRE
classic: 1h × 14.4 burn AND 5min × 14.4 burn) needs a defined error
budget that doesn't exist yet. **Upgrade path:** when an SRE-style
error budget lands, replace this single rule with two rules per the
multi-window pattern.

#### Guardrail trip (severity 1 — page now)

```kql
customEvents
| where timestamp > ago(5m)
| where name in ('guardrail.cost_cap', 'guardrail.loop_cap')
| summarize trip_count = count() by name
| where trip_count > 0
```

#### Hallucination spike (severity 2)

```kql
let win = 1h;
let llm = dependencies
  | where timestamp > ago(win)
  | where customDimensions['openinference.span.kind'] == 'LLM'
     or customDimensions['gen_ai.system'] == 'openai';
let refusals = customEvents
  | where timestamp > ago(win)
  | where name == 'llm.refused';
let total = toscalar(llm | count);
let refused = toscalar(refusals | count);
print
  total = total,
  refused = refused,
  refusal_pct = iff(total == 0, 100.0, todouble(refused) / total * 100)
| where total > <hallucinationSpikeMinCalls> and refusal_pct < 1.0
```

`<hallucinationSpikeMinCalls>` is a Bicep param (default 10) — raise
it once steady-state prod LLM traffic is known. The `iff(total == 0,
100.0, …)` clause inside the KQL is a belt-and-suspenders defense
against the `param=0` edge case.

### CustomEvents contracts

#### Guardrail events (CM-28 stop-rules will emit)

```python
from opentelemetry import trace
tracer = trace.get_tracer(__name__)

# When the per-request cost cap is hit:
with tracer.start_as_current_span("guardrail.cost_cap") as span:
    span.set_attribute("guardrail.budget_usd", budget_usd)
    span.set_attribute("guardrail.spent_usd", spent_usd)
    span.set_attribute("hitl.task_id", task_id)  # if applicable

# When the per-request loop cap is hit:
with tracer.start_as_current_span("guardrail.loop_cap") as span:
    span.set_attribute("guardrail.max_loops", max_loops)
    span.set_attribute("guardrail.loop_count", loop_count)
```

#### Refusal events (CM-30 Triage will emit)

```python
# When the model declines (content filter, "I don't know" path, etc.):
with tracer.start_as_current_span("llm.refused") as span:
    span.set_attribute("llm.refusal_reason", reason)
    span.set_attribute("gen_ai.request.model", model)
```

Both contracts use OTel `tracer.start_as_current_span(...)` which lands
in App Insights as a row in `customEvents`. The literal event names
(`guardrail.cost_cap`, `guardrail.loop_cap`, `llm.refused`) are the
ones the alert rules query for — stay on those names so the alerts
fire automatically when the producer story ships.

#### PRD success-metric events (CM-39 / CM-46)

`agents.observability.emit_metric(name, value=1.0, **attrs)` lands one
`customEvents` row per PRD signal — same table + KQL the CM-25 workbook
queries. Every event carries `metric.value` + correlation (`request_id`,
`tenant_id`); `None` attrs are dropped. No-op-safe when OTel is unconfigured.
The event-name constants live in `agents/observability/metrics.py`; emission
lives in the orchestrator node next to the span/guardrail contract (the one
exception is ack-latency, emitted at the channel-adapter entry layer).

| Event name | Emitted by | `value` | Key attrs |
|---|---|---|---|
| `metric.triage.route` | triage node | 1.0 | `intent`, `route` |
| `metric.knowledge.answered` / `.refused` | knowledge node | 1.0 | `confidence` |
| `metric.maintenance.dedup` | maintenance node | 1.0 | `outcome` ∈ {new, duplicate}, `category`, `is_repeat` |
| `metric.vendor.auto_dispatch` / `.hitl` | vendor node | 1.0 | `category`, `vendor_id` |
| `metric.escalation.legal_flag` | escalation node (only when flagged) | 1.0 | `category`, `severity` |
| `metric.ack_latency_ms` | `WebAdapter` (entry layer) | channel→us latency ms | `channel`, `tenant_id` |
| `metric.ttm_resolution_ms` | `resolve_ticket()` | `resolved_at − created_at` ms (≥0) | `category`, `priority` |
| `metric.followup` | maintenance node (recurrence vs RESOLVED) | 1.0 | `category`, `prior_ticket_id` |
| `metric.hitl.rating` | `hitl_review` node | rating or 1.0 | `decision`, `category`, `legal_risk`, `has_rating` |

The TTM + follow-up events are the **outcome** metrics — they only accrue once
tickets are resolved (`Ticket.resolved_at`, stamped by the
`TicketRepository.resolve()` seam). `agents.analytics.ttm_baseline()` /
`followup_rate()` compute the current baselines from the `tickets` store;
`infra/scripts/outcome-baselines.py` prints them for an operator (reporting
"pending data" rather than a fabricated number when there are no resolutions
yet). Efficiency gain has no runtime event — it is a manual manager time study.

### Why RG-scoped budget pre-OpenAI

The AC mentions "monthly Azure OpenAI spend" but no
`Microsoft.CognitiveServices/accounts` (Azure OpenAI) resource exists
in the project yet. RG-scoped budget catches every paid resource in
`rg-condomanager`; in phase 0 that's tiny container-app runtime + a
trickle of Log Analytics ingestion. When CM-OpenAI lands, OpenAI
tokens dominate the bill anyway — operator can tighten the filter to
`category: 'ResourceGroupName' + ResourceType` in a follow-up. No
work is wasted.

### Cost of running the alerts themselves

Three scheduled-query rules × max ~12 evaluations/hour × small KQL
windows = well under $1/month in phase 0 (Azure Monitor logs query
charges, not row counts at this volume). Documented for the cost-paranoid
operator; the budget alert above catches surprises.

## What comes next (forward references)

* **CM-28** is the first consumer of `langgraph_node_span` and
  `with_agent()`, and the first emitter of `guardrail.cost_cap` /
  `guardrail.loop_cap`. The per-agent error panel (CM-25 workbook),
  the latency-SLO panel + alert (CM-26), and the guardrail-trip alert
  all light up once CM-28 ships traffic. `agent_name` flows into log
  lines (CM-27) and Langfuse observations (CM-24) alike.
* **CM-30** extends `tests/eval/triage_seed.jsonl` to 200 examples and
  emits `llm.refused` from the refusal path — the hallucination-spike
  alert depends on this.
