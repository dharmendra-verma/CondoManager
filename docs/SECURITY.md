# Security & Compliance — CM-38

> Jira: **CM-38** | Epic: CM-Epic 13 (Security & Compliance) | Phase 1

This doc is the operator/auditor reference for how CondoManager handles PII and
the controls that back a SOC2 Type II audit. The code lives in
`agents/security/` (+ the CM-27 log masker it builds on) and
`infra/bicep/modules/{cosmos,cosmos-rbac}.bicep`.

## 1. PII detection (AC1)

`agents/security/detection.py` exposes a `PiiDetector` seam:

| Implementation | When | Categories | Notes |
|---|---|---|---|
| `RegexPiiDetector` (default) | offline / CI / no `AI_LANGUAGE_ENDPOINT` | email, phone (E.164), credit card (Luhn), API key | deterministic, no network |
| `AzureLanguagePiiDetector` | `AI_LANGUAGE_ENDPOINT` set | + person, address, government IDs | lazy `azure-ai-textanalytics`; AAD via the CM-18 MI |

`get_pii_detector()` selects by env var (cached singleton, like
`get_ticket_repository`). The Azure SDK is an **optional** install
(`pip install -e ".[security]"`) and is never loaded in CI.

> **Honest scope.** The regex detector does **not** emit `person`/`address`/
> `gov_id` — those require the Azure detector. The offline eval
> (`tests/security/test_pii_eval.py`) gates recall at 100% only for the four
> categories the regex path is responsible for.

## 2. Masking at the log + trace layers (AC2)

`mask_text()` (`agents/security/masking.py`) is the **single masking facade**;
it delegates to CM-27 `mask_pii` so there is one implementation across all
layers (no drift):

- **Log layer** — CM-27 `PiiMaskingFilter`, installed by `configure_logging`.
- **Trace layer** — `PiiMaskingSpanProcessor`, registered in
  `setup_tracer_provider` (CM-21). It masks string-valued span attributes in
  `on_end`, **before** the exporting processor runs.
- **Audit detail / notifier free text** — both route through `mask_text`.

> **Batch-ordering note.** On the console/OTLP path the masking processor is
> registered before the exporting `SimpleSpanProcessor`/`BatchSpanProcessor`,
> so its synchronous `on_end` mutation completes first. On the Azure Monitor
> path the distro adds its own `BatchSpanProcessor`; masking still runs
> synchronously in `on_end` while export is deferred to the batch worker (5 s
> timer), so attributes are masked before serialization. The masker swallows
> all internal errors — masking must never break tracing.

## 3. Field-level access control in Cosmos DB (AC3)

**Cosmos DB has no native field/column-level RBAC.** The control is split:

- **Azure side** — `infra/bicep/modules/cosmos-rbac.bicep` grants the CM-18
  Managed Identity the built-in *Cosmos DB Data Contributor* role scoped to the
  `condomanager` database, so workloads authenticate via AAD
  (`DefaultAzureCredential`) instead of account keys. Granularity:
  account/database/container only.
- **Application side** — `agents/security/field_access.py::redact_document`
  enforces *field*-level visibility per `AccessRole`. PII fields are redacted
  for roles that may not see them; unknown roles **fail closed** (no PII).

| Role | Sees PII? |
|---|---|
| `auditor`, `manager` | yes (contact info) |
| `agent` | only the free-text request fields (`issue_text`, `summary`) |
| `analytics`, `tenant`, unknown | no raw PII |

## 4. Audit log with immutable retention (AC4)

`agents/security/audit.py` — `record_audit(...)` masks free-text `detail`, stamps an
id/timestamp, and writes an immutable `AuditEvent` through an append-only
`AuditSink` (`InMemoryAuditSink` offline, `CosmosAuditSink` over the `audit`
container).

**Cosmos has no native WORM.** Immutability is approximated by three layers:

1. `AuditEvent` is `frozen` (no in-code mutation).
2. The sink is **write-only** — no update/delete method; `CosmosAuditSink` uses
   `create_item` (not `upsert`), so an id replay is rejected, never overwritten.
3. The `audit` container uses `defaultTtl: -1` (never auto-purges) and rides
   Cosmos continuous backup.

> **Follow-up (not in CM-38):** a true tamper-evident trail (per-record hash
> chain or export to an append-only store / immutable blob with a legal hold)
> is tracked under CM-Epic 13 hardening.

## 5. Data retention + deletion policies (AC6)

`agents/security/retention.py`:

- `RETENTION_POLICY` — the per-container retention source of truth, kept in
  sync with `cosmos.bicep` `defaultTtl` values:

  | Container | Retention | TTL source |
  |---|---|---|
  | `checkpoints` | 30 days | CM-28 |
  | `digests` | 90 days | CM-36 |
  | `audit`, `escalations` | never expires | CM-38 / CM-32 |
  | `tenants`, `tickets`, `conversations`, … | until tenant erasure | — |

- `delete_tenant_data(tenant_id, sources=...)` — the right-to-erasure routine.
  It fans out over the `ErasableSource` for each tenant-data container and is
  **non-blocking**: one source failing is recorded in the `DeletionReport`
  (`complete=False`) and the rest still run, so erasure is auditable and
  retryable rather than silently half-done.

## 6. SOC2 Common-Criteria control matrix (CC1–CC9) — AC5

| Criteria | Control | Implemented by | Status / gap |
|---|---|---|---|
| **CC1** Control environment | Org owns the platform; settled decisions in `CLAUDE.md`/`AGENT_RULES.md`; per-PR self-review (7-point) | repo governance | ✅ |
| **CC2** Communication & information | Structured JSON logs + correlation ids; this doc + `docs/INFRA.md`/`docs/OBSERVABILITY.md` | CM-21/27 | ✅ |
| **CC3** Risk assessment | Threat modeling + pen-test prep | — | ⛔ deferred (CM-Epic 13) |
| **CC4** Monitoring | App Insights spans, alerts, ops workbook | CM-22/25/26 | ✅ |
| **CC5** Control activities | IaC reviews, branch protection + required reviewers on `prod` | CM-19/40 | 🟡 CM-40 in flight |
| **CC6** Logical & physical access | AAD-only data-plane RBAC (no account keys), KV RBAC + purge protection, field-level redaction | CM-18, **CM-38 §3** | ✅ (key-auth disable = follow-up) |
| **CC7** System operations | PII masking at log + trace layers; audit logging of data access | **CM-38 §2/§4** | ✅ |
| **CC8** Change management | Conventional Commits + Jira key, CI gates (lint/test/what-if), GitHub Environments | CM-15/19 | ✅ |
| **CC9** Risk mitigation | Data retention + right-to-erasure; budget guardrails | **CM-38 §5**, CM-26 | ✅ |

**Privacy (P-series) note:** PII detection (§1), masking (§2), access limitation
(§3), and erasure (§5) cover the core privacy commitments; a formal privacy
notice + consent flow is a product/legal deliverable, not engineering, and is
out of scope here.

## 7. Open gaps (tracked, not silently dropped)

- Threat modeling + pen-test (CC3) — CM-Epic 13.
- Disable Cosmos key-based auth once all workloads are confirmed on AAD.
- Tamper-evident audit (hash chain / immutable export) — CM-Epic 13.
- `prod` required reviewers — CM-40 (in flight).
- Live Azure AI Language detector has no integration test (seam only); it is
  exercised via unit-level mapping, not a live call.
