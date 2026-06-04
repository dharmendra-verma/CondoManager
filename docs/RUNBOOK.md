# RUNBOOK — Condo Manager

> Jira: CM-54 | Phase 0 — End-to-end deploy & private-beta smoke test
>
> This document is the operator's start-to-finish procedure: bring up the dev
> environment, seed it, smoke-test it, run live channel tests, verify
> observability, and tear it down safely. For architecture detail see
> `docs/INFRA.md`; for CI/CD detail see `docs/CICD.md`.

---

## Prod — live app URLs & how to use it (CM-59)

CM-59 deployed the **real** runtime to prod: the agent-runtime container image
(replacing the hello-world placeholder), the tenant portal on Static Web Apps,
and the CM-55 web-chat channel as the public inbound entry point.

| Surface | URL |
|---|---|
| **Agent runtime / web chat** (Container App) | `https://ca-hello-condomanager-prod.ambitiousbay-0a96856a.eastus2.azurecontainerapps.io` |
| **Tenant portal** (Static Web App) | `https://wonderful-pebble-094fa600f.7.azurestaticapps.net` |

> ⚠️ The web chat is a **TEST** channel: hardcoded `mobile → tenant` map
> (`agents/webchat/tenants.py`), no OTP/real auth (CM-55/CM-56 own that). It is
> the prod demo/smoke entry point, not a real tenant channel. Container App
> scale-to-zero means the first request after idle has a cold-start delay.

**Container App endpoints** — `GET /healthz` (always 200), `POST /web/login`,
`POST /web/message` (the last two 404 unless `WEBCHAT_TEST_ENABLED=1`, which
prod sets):

```bash
BASE="https://ca-hello-condomanager-prod.ambitiousbay-0a96856a.eastus2.azurecontainerapps.io"

curl -s "$BASE/healthz"                        # -> {"status":"ok","channel_enabled":true}

curl -s -X POST "$BASE/web/login" \
  -H 'content-type: application/json' \
  -d '{"mobile":"+919876543210"}'              # -> {"tenant_id":"condo-tower-a","name":"Asha Rao","unit":"4B"}

curl -s -X POST "$BASE/web/message" \
  -H 'content-type: application/json' \
  -d '{"mobile":"+919876543210","content":"The kitchen tap in unit 4B is leaking."}'
# -> {"reply":"...","stub":<bool>,"channel":"web","intent":"...","masked_content":"..."}
```

Test tenant mobiles: `+919876543210` (Asha Rao, 4B), `+919812345678`
(Vikram Singh, 2A), `+14155550100` (Jordan Lee, 12C). A `stub:true` reply means
the live agent loop degraded gracefully (e.g. no LLM creds) but the message still
flowed end to end; `stub:false` is a real agent reply.

**Browser chat (no curl, no local dev — CM-60):** open
`https://wonderful-pebble-094fa600f.7.azurestaticapps.net/test-chat.html`, enter a
test mobile (e.g. `+919876543210`), and chat. The page calls the Container App
API cross-origin — the prod build bakes in `VITE_WEBCHAT_API_BASE` (the Container
App URL) and the agent app's CORS allows the SWA origin via `WEBCHAT_CORS_ORIGINS`.
It's still the TEST channel (hardcoded tenant map, no OTP).

**Scripted smoke test** (asserts the whole flow, then prints the App Insights
KQL to confirm the trace landed):

```bash
az login                                       # reader on rg-condomanager
bash infra/scripts/smoke-test-prod.sh
```

**How it deploys:** push to `main` → `deploy.yml` → `build-agent-image`
(`az acr build` → `acrcondomanagerprod.azurecr.io/agent:<sha>`) →
`deploy-prod` (Bicep with `agentImage=<tag>`, MI pulls via AcrPull, port 8000,
`WEBCHAT_TEST_ENABLED=1`) → `deploy-portal-prod` (gated on
`PORTAL_DEPLOY_ENABLED=true`, now set; SWA token via OIDC). See `docs/CICD.md`.

---

## 0. Audience & prerequisites

**Audience:** platform-team operator with Owner or Contributor access to the
Azure subscription and admin access to the GitHub repository.

**Tools required (install before starting):**

| Tool | Version | Install |
|---|---|---|
| Azure CLI | ≥ 2.60 | [learn.microsoft.com/cli/azure](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) |
| Bicep CLI | ≥ 0.29 | `az bicep install` |
| Python | 3.12 | python.org |
| GitHub CLI (`gh`) | ≥ 2.40 | [cli.github.com](https://cli.github.com) |
| Azure Functions Core Tools | v4 | `npm install -g azure-functions-core-tools@4` |
| Node.js | ≥ 18 | nodejs.org |

**Python packages:**

```bash
pip install "azure-cosmos>=4.7.0" "azure-identity>=1.15.0" \
            "azure-keyvault-secrets>=4.7.0"
```

**Azure login:**

```bash
az login
az account set --subscription "<your-subscription-id>"
```

---

## 1. One-time bootstrap

> Skip this section if the Azure AD app, federated credentials, and GitHub
> Environments already exist. Run `az ad app list --display-name condomanager`
> to check. Full detail in `docs/CICD.md § Bootstrap`.

### 1.1 Azure OIDC setup

Run from Azure Cloud Shell or a local terminal with Owner role:

```bash
bash infra/scripts/setup-azure-oidc.sh
```

This script creates:
- An Azure AD app + service principal named `condomanager-cicd`
- 4 federated credentials (pull_request, push:main, environment:dev, environment:prod)
- Role assignments: `Contributor` + `User Access Admin` on the subscription
- Provider registrations: `Microsoft.App`, `Microsoft.Web`, `Microsoft.DocumentDB`, etc.

**Verify:** `az ad app list --display-name condomanager-cicd --query "[].appId"` returns one app ID.

### 1.2 GitHub Environments

```bash
bash infra/scripts/setup-github-environments.sh
```

Creates `dev` (auto-deploy) and `prod` (required-reviewer) GitHub Environments.

**Verify:** Navigate to GitHub → Settings → Environments; `dev` and `prod` appear,
`prod` has at least one required reviewer.

### 1.3 GitHub repository secrets

In GitHub → Settings → Secrets → Actions, confirm these three secrets exist:

| Secret | Value |
|---|---|
| `AZURE_CLIENT_ID` | App ID from step 1.1 |
| `AZURE_TENANT_ID` | Your Azure tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Your Azure subscription ID |

Set them with the GitHub CLI if missing:

```bash
gh secret set AZURE_CLIENT_ID     --body "<app-id>"
gh secret set AZURE_TENANT_ID     --body "<tenant-id>"
gh secret set AZURE_SUBSCRIPTION_ID --body "<subscription-id>"
```

---

## 2. Deploy dev environment

### 2.1 Via CI/CD (recommended)

Push any change to `infra/` on `main` to trigger `deploy.yml → deploy-dev`:

```bash
git checkout main && git pull
# Make a no-op change or push your feature branch to main via PR:
gh pr merge <pr-number> --merge
```

Watch the run: `gh run watch` or GitHub → Actions → deploy.yml.

**Expected:** job `deploy-dev` completes green in ~4 minutes. All 18 Bicep
modules (+ new `vendors` container) are deployed.

### 2.2 Manual fallback (without CI)

```bash
az deployment group create \
  --resource-group rg-condomanager \
  --template-file infra/bicep/main.bicep \
  --parameters infra/bicep/main.parameters.json \
  --parameters env=dev \
  --name "cm-manual-$(date +%Y%m%d%H%M)"
```

**Verify resources exist:**

```bash
az cosmosdb show --name cosmos-condomanager-dev \
                 --resource-group rg-condomanager \
                 --query "documentEndpoint" -o tsv

az keyvault show --name kv-condomanager-dev \
                 --resource-group rg-condomanager \
                 --query "properties.vaultUri" -o tsv
```

### 2.3 Re-verify pre-prod gates (clean-run check)

On each fresh deploy, confirm these previously-fixed issues haven't regressed:

```bash
# CM-41: Azure provider registration
az provider show --namespace Microsoft.App --query "registrationState" -o tsv
# Expected: Registered

# CM-43: Key Vault role assignment — MI can read secrets
az role assignment list \
  --assignee "$(az identity show -n id-condomanager-dev -g rg-condomanager --query principalId -o tsv)" \
  --role "Key Vault Secrets User" \
  --scope "$(az keyvault show -n kv-condomanager-dev -g rg-condomanager --query id -o tsv)" \
  --query "[].roleDefinitionName" -o tsv
# Expected: Key Vault Secrets User
```

---

## 3. Populate Key Vault secrets

### 3.1 Seed placeholder values

```bash
bash infra/scripts/seed-keyvault-secrets.sh kv-condomanager-dev
```

Sets `REPLACE-ME` for all 11 secrets. Idempotent — skips secrets already set.

### 3.2 Replace placeholders with real values

For each secret below, obtain the real value from the respective service console
and set it **out-of-band** (never in git, never in CI logs):

```bash
VAULT=kv-condomanager-dev

az keyvault secret set --vault-name $VAULT \
  --name azure-openai-key --value "<Azure OpenAI key from portal>"

az keyvault secret set --vault-name $VAULT \
  --name twilio-account-sid --value "<Twilio Account SID>"

az keyvault secret set --vault-name $VAULT \
  --name twilio-auth-token --value "<Twilio Auth Token>"

az keyvault secret set --vault-name $VAULT \
  --name twilio-whatsapp-number --value "+1415XXXXXXX"

az keyvault secret set --vault-name $VAULT \
  --name langsmith-api-key --value "<LangSmith API key>"

az keyvault secret set --vault-name $VAULT \
  --name langfuse-public-key --value "<Langfuse public key>"

az keyvault secret set --vault-name $VAULT \
  --name langfuse-secret-key --value "<Langfuse secret key>"

az keyvault secret set --vault-name $VAULT \
  --name slack-webhook-url --value "<Slack incoming webhook URL>"

az keyvault secret set --vault-name $VAULT \
  --name google-drive-sa-key --value "$(cat /path/to/service-account.json)"

COSMOS_CONN=$(az cosmosdb keys list \
  --name cosmos-condomanager-dev \
  --resource-group rg-condomanager \
  --type connection-strings \
  --query 'connectionStrings[0].connectionString' -o tsv)
[[ -z "$COSMOS_CONN" ]] && { echo "ERROR: failed to retrieve Cosmos connection string — check deployment and az login"; exit 1; }
az keyvault secret set --vault-name $VAULT \
  --name cosmos-connection-string \
  --value "$COSMOS_CONN"
```

### 3.3 Seed App Insights connection string

```bash
bash infra/scripts/seed-app-insights-secret.sh \
  kv-condomanager-dev \
  rg-condomanager \
  appi-condomanager-dev
```

### 3.4 Seed LangSmith evaluation dataset

```bash
export LANGCHAIN_API_KEY="$(az keyvault secret show \
  --vault-name kv-condomanager-dev --name langsmith-api-key --query value -o tsv)"
python infra/scripts/seed-langsmith-dataset.py
```

**Verify:** Navigate to LangSmith → Datasets; `condomanager-triage-eval` appears with rows.

---

## 4. Seed data

### 4.1 Vendor roster + test tenant

Run from the repository root:

```bash
export COSMOS_ENDPOINT="$(az cosmosdb show \
  --name cosmos-condomanager-dev \
  --resource-group rg-condomanager \
  --query documentEndpoint -o tsv)"

python infra/scripts/seed-cosmos.py
```

**Expected output:**

```
-> Endpoint: https://cosmos-condomanager-dev.documents.azure.com:443/
-> Database: condomanager
  [upsert] vendor v-plumb-1 (AquaFix Plumbing)
  [upsert] vendor v-plumb-2 (Premium Pipes)
  [upsert] vendor v-elec-1 (BoltWorks Electric)
  [upsert] vendor v-hvac-1 (ClimateCare HVAC)
  [upsert] vendor v-appliance-1 (FixIt Appliances)
  [upsert] vendor v-struct-1 (SolidBuild Structural)
  [upsert] vendor v-general-1 (Handy General Services)
  [upsert] tenant tenant-smoke-test
  [upsert] ticket TKT-1A2B3C4D

PASS: seeded 7 vendors, 1 test tenant, 1 sample ticket (TKT-1A2B3C4D).
```

Idempotent — safe to re-run. Second run produces identical output with no errors.

### 4.2 Knowledge base (Google Drive → Cosmos)

Trigger the `gdrive-sync` Azure Function to pull policy documents into the
`policies-vector` container:

```bash
# Invoke the timer trigger immediately via the Kudu admin API.
# Get the master key first (required for admin endpoint):
MASTER_KEY=$(az functionapp keys list \
  --name func-condomanager-dev \
  --resource-group rg-condomanager \
  --query "masterKey" -o tsv)

# Verify the key was retrieved before sending (empty key → 401 with no output):
[[ -z "$MASTER_KEY" ]] && { echo "ERROR: master key is empty — check az login and RBAC"; exit 1; }

curl -s -w "\nHTTP %{http_code}\n" -X POST \
  "https://func-condomanager-dev.azurewebsites.net/admin/functions/gdrive_sync" \
  -H "x-functions-key: $MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
# Expected: HTTP 202  (empty body is normal — 202 means the trigger was accepted)
```

> **Note:** `az functionapp restart` recycles the host process but does NOT invoke
> the function — the timer fires only on its next scheduled occurrence (every 30 min).
> The admin API `POST /admin/functions/<name>` triggers an immediate run on any plan.

**Verify:** Wait ~2 minutes, then check the `knowledge_sync` container in Cosmos
Data Explorer or via CLI:

```bash
az cosmosdb sql query \
  --account-name cosmos-condomanager-dev \
  --resource-group rg-condomanager \
  --database-name condomanager \
  --container-name knowledge_sync \
  --query "SELECT * FROM c" \
  --output table
```

Expected: at least one row with `source` = the configured Google Drive folder ID
and a non-null `lastPageToken`.

---

## 5. Smoke tests

Run all three smoke tests in order. Each exits 0 on pass, 1 on failure.

### 5.1 Cosmos DB vector search

```bash
export COSMOS_ENDPOINT="$(az cosmosdb show \
  --name cosmos-condomanager-dev \
  --resource-group rg-condomanager \
  --query documentEndpoint -o tsv)"

export COSMOS_KEY="$(az cosmosdb keys list \
  --name cosmos-condomanager-dev \
  --resource-group rg-condomanager \
  --query primaryMasterKey -o tsv)"

python infra/scripts/cosmos-smoke-test.py
```

**Expected:**

```
>  Inserting smoke-test doc id=smoke-<uuid> into condomanager/policies-vector
>  Running VectorDistance() query
   OK nearest match: id=smoke-<uuid> cosine-similarity=1.0
PASS: Cosmos vector-search smoke-test
>  Deleted smoke-test doc id=smoke-<uuid>
```

The cosine similarity score must be ≥ 0.99 (post-CM-47: VectorDistance returns
a *similarity*, not a distance — identical vectors score ≈ 1.0).

### 5.2 Key Vault + Managed Identity

```bash
export KEYVAULT_NAME=kv-condomanager-dev
python infra/scripts/keyvault-smoke-test.py
```

**Expected:** `PASS Key Vault + Managed Identity round-trip works.`

If you see `403/Forbidden`: the calling principal lacks `Key Vault Secrets User`
on the vault (check role assignment via step 2.3).

### 5.3 Google Drive sync idempotency

```bash
export COSMOS_ENDPOINT="..."   # same as 5.1
export COSMOS_KEY="..."
python infra/scripts/gdrive-sync-smoke-test.py
```

**Expected:** `PASS: gdrive-sync smoke-test` — runs an in-process fake Drive +
stub embedder, asserts idempotency (running twice does not duplicate documents).

---

## 6. Live channel test — Web

> **Status:** Available now (WebAdapter implemented in CM-29).

The Web channel accepts a JSON POST to the Container App's inbound webhook
endpoint. The Container App currently runs the hello-world image (actual agent
code is deployed in a later story); this section documents the procedure to
follow once the agent image is deployed.

### 6.1 Get the Container App FQDN

```bash
FQDN=$(az containerapp show \
  --name ca-hello-condomanager-dev \
  --resource-group rg-condomanager \
  --query "properties.configuration.ingress.fqdn" -o tsv)
echo "https://$FQDN"
```

### 6.2 Send a test message

```bash
curl -s -X POST "https://$FQDN/inbound/web" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "tenant-smoke-test",
    "sender_id": "unit-101",
    "content": "The kitchen tap is dripping constantly.",
    "upstream_message_id": "web-smoke-001"
  }'
```

**Expected:** HTTP 200 with a JSON body containing `ticket_id` and `reply`.

### 6.3 Verify the full loop

For each test message sent:

1. **NormalizedMessage correct** — `channel=WEB`, `tenant_id`, `sender_id` populated.
2. **Triage routes correctly** — maintenance intent → `maintenance` route (check App Insights trace or log).
3. **Ticket created** — `ticket_id` appears in the `tickets` Cosmos container:
   ```bash
   # Query tickets container for the smoke tenant:
   az cosmosdb sql query \
     --account-name cosmos-condomanager-dev \
     --resource-group rg-condomanager \
     --database-name condomanager \
     --container-name tickets \
     --query "SELECT c.id, c.status, c.category FROM c WHERE c.tenantId='tenant-smoke-test'"
   ```
4. **Vendor dispatched / HITL queued** — check `escalations` container for HITL records or logs for dispatch confirmation.
5. **Outbound reply received** — HTTP response body contains the tenant-facing reply text.

---

## 7. Live channel tests — WhatsApp / Telegram / Email

> **Status: [pending CM-31 / CM-32 / CM-33]**
>
> WhatsApp, Telegram, and Email channel adapters are not yet implemented.
> This section is a placeholder. Fill in each sub-section when the corresponding
> story ships.

### 7.1 WhatsApp (requires CM-31)

```bash
# Prerequisites: Twilio sandbox configured, test phone number registered.
# Send a WhatsApp message to the Twilio sandbox number from your test phone.
# Twilio delivers a webhook POST to:
#   https://<FQDN>/inbound/whatsapp
#
# Verify NormalizedMessage, ticket creation, and outbound reply receipt on phone.
```

Voice note (send a WhatsApp voice note from your test phone):
- Verify `NormalizedMessage.attachments` contains the audio media URL.
- Verify transcription appears in the ticket body (requires CM-35).

Photo (send a WhatsApp photo):
- Verify `NormalizedMessage.attachments` contains the image media URL.
- Verify OCR text appears in the ticket body (requires CM-35).

### 7.2 Telegram (requires CM-32)

```bash
# Prerequisites: Telegram bot created via BotFather, webhook registered.
# Send a message to the bot from your test Telegram account.
# Bot webhook delivers a POST to:
#   https://<FQDN>/inbound/telegram
#
# Verify NormalizedMessage, ticket creation, and outbound reply in Telegram chat.
```

### 7.3 Email (requires CM-33)

```bash
# Prerequisites: IMAP mailbox configured (host/user/pass in KV), polling active.
# Send an email to the configured inbound address from a test account.
#
# Verify NormalizedMessage (subject → content), ticket creation, and outbound reply.
```

---

## 8. Media: voice note + photo

> **Status: [pending CM-35]**
>
> AudioTranscriber and ImageOcr are stubs. This section documents the
> verification procedure once CM-35 ships.

```bash
# Voice note — send via WhatsApp (see §7.1):
# Verify ticket body contains the transcription text (not the raw media URL).

# Photo — send via WhatsApp (see §7.1):
# Verify ticket body contains extracted text from the image (OCR result).
```

---

## 9. Verify observability

### 9.1 App Insights — find a request_id span

After sending a test message (§6 or §7), locate the trace in App Insights:

```bash
# Get the App Insights app ID:
APP_ID=$(az monitor app-insights component show \
  --app appi-condomanager-dev \
  --resource-group rg-condomanager \
  --query appId -o tsv)

# Query traces for the last 15 minutes (replace with your request_id):
az monitor app-insights query \
  --app $APP_ID \
  --analytics-query "traces | where customDimensions.request_id == '<request_id>' | project timestamp, message, customDimensions | order by timestamp asc"
```

**Expected:** rows from `triage`, `maintenance` (or `knowledge`/`escalation`),
`vendor` nodes — each sharing the same `request_id`.

Alternatively: Azure Portal → Application Insights → Transaction search → filter
by `request_id` custom property.

### 9.2 LangSmith — find a matching trace

1. Navigate to [smith.langchain.com](https://smith.langchain.com) → project `condomanager-dev`.
2. Search for the `request_id` value in the trace metadata.
3. Confirm the full agent chain is visible (triage → downstream node).

### 9.3 Langfuse — confirm LLM cost/quality events

1. Navigate to your Langfuse dashboard (cloud.langfuse.com or self-hosted).
2. Filter traces by `request_id`.
3. Confirm token counts and latency are captured per LLM call.

> **Note:** Langfuse is enabled only when `LANGFUSE_PUBLIC_KEY` and
> `LANGFUSE_SECRET_KEY` are set to real values in Key Vault (not `REPLACE-ME`).

---

## 10. Trigger & verify guardrail alert

The guardrail fires when a single request exceeds $5.00 in LLM cost or 50
search/agent steps. To verify the alert pipeline in dev:

### 10.1 Manually emit a guardrail event

The alert rule fires on the `guardrail.cost_cap` or `guardrail.loop_cap`
custom event in App Insights. Emit one directly:

```python
# Run from the repo root (requires APPLICATIONINSIGHTS_CONNECTION_STRING env var):
from opentelemetry import trace
from agents.observability.sdk import configure_telemetry
configure_telemetry()
tracer = trace.get_tracer("smoke-test")
with tracer.start_as_current_span("guardrail.cost_cap") as span:
    span.set_attribute("guardrail", "cost_cap")
    span.set_attribute("cost_so_far", 5.01)
    span.set_attribute("request_id", "smoke-guardrail-001")
print("Guardrail event emitted.")
```

### 10.2 Verify the alert fired

The `scheduledQueryRules` resource does not expose a last-fired timestamp via the
CLI — the resource metadata only tracks when the rule definition was last modified.
Use the two reliable verification paths instead:

**Primary — Action Group delivery (fastest):**
Check for the Slack message or email delivered by the Action Group configured in §3.2.
The alert fires within ~5 minutes of the evaluation window (every 5 minutes per the
alert rule configuration).

**Secondary — Azure Monitor Activity Log:**
```bash
# Look for alert-fired events in the last 30 minutes:
az monitor activity-log list \
  --resource-group rg-condomanager \
  --start-time "$(date -u -d '30 minutes ago' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
                  || date -u -v-30M +%Y-%m-%dT%H:%M:%SZ)" \
  --query "[?contains(operationName.value, 'Microsoft.Insights')].{time:eventTimestamp, op:operationName.value, status:status.value}" \
  --output table
```

**Expected:** an entry with `operationName` containing `scheduledQueryRules` and
`status` = `Succeeded` within the last 10 minutes.

---

## 11. Tenant portal verification

### 11.1 Get the Static Web App URL

```bash
SWA_URL=$(az staticwebapp show \
  --name swa-condomanager-dev \
  --resource-group rg-condomanager \
  --query "defaultHostname" -o tsv)
echo "https://$SWA_URL"
```

### 11.2 Look up a ticket by share code

After a ticket is created in §6.3, retrieve its share code from Cosmos:

```bash
# The ticket document has a `shareCode` field (TKT-XXXXXXXX format):
az cosmosdb sql query \
  --account-name cosmos-condomanager-dev \
  --resource-group rg-condomanager \
  --database-name condomanager \
  --container-name tickets \
  --query "SELECT c.id, c.shareCode, c.status FROM c WHERE c.tenantId='tenant-smoke-test'" \
  --output table
```

Then call the portal API:

```bash
curl -s "https://$SWA_URL/api/ticket?code=TKT-XXXXXXXX" | python -m json.tool
```

**Expected:** JSON with `id`, `status`, `category`, `summary` fields — no internal fields exposed.

### 11.3 Visual check

Open `https://<SWA_URL>/status?code=TKT-XXXXXXXX` in a browser. The ticket
status card should render with the correct summary and status.

### 11.4 Day-zero check using the seeded sample ticket (no live loop required)

`seed-cosmos.py` (§4.1) upserts one ready-made ticket so the portal can be
verified before the live channel→triage loop (CM-31/32/33/35) exists. The portal
API (CM-37) looks up tickets by document `id`:

```bash
curl -s "https://$SWA_URL/api/ticket?code=TKT-1A2B3C4D" | python -m json.tool
```

**Expected:** HTTP 200 with `id` = `TKT-1A2B3C4D`, `status` = `In Progress`,
`eta` = `Tomorrow, 2-4 PM`, `owner` = `AquaFix Plumbing`.

> **Note:** §11.2 assumes a live ticket created by the channel loop. Use §11.4
> for the deterministic day-zero smoke test before those channels are wired up.

### 11.5 Tenant admin page (`/admin`)

The admin page (`https://<SWA_URL>/admin`, CM-56) does tenant master CRUD via the
managed Functions API at **`/api/tenants`** (collection) and `/api/tenants/{id}`
(item). Three things must hold for it to work — the first is code, the other two
are operator-set app settings:

1. **Function registration + route placement (code, CM-61).** Two distinct SWA
   gotchas bit this:
   - Every `app.http(...)` registration MUST live in the `package.json` `"main"`
     entry file (`portal/api/src/index.ts`). Registrations in another module
     pulled in via a side-effect `import` or a multi-file `main` glob are **not
     served** by the SWA managed-functions host (its bundling drops them) — even
     though they still show up in the ARM functions list.
   - The route must **not** sit under `/api/admin/*`. The SWA edge does not
     forward `/api/admin/*` paths to the Functions backend — they return a **bare
     404** (`Content-Length: 0`, `x-ms-middleware-request-id`, no body) before
     reaching any function. `/api/<non-admin>` paths (incl. multi-segment like
     `/api/tenants/{id}`) forward fine; exact rules like `/api/ticket` are also
     fine. Verified with live probes. Hence the API lives at `/api/tenants`, and
     `staticwebapp.config.json` no longer has an `/api/admin/*` route rule.
2. **`TENANT_ADMIN_ENABLED=1`** app setting on `swa-condomanager-prod`. The
   handlers are fail-closed: without it every call returns the handler's own
   `404 {"error":"not_found"}` JSON (note: a *JSON* 404, distinct from the bare
   edge 404 above).
3. **`COSMOS_CONNECTION_STRING`** app setting (+ the `tenants` Cosmos container).
   Unset/`REPLACE-ME` falls back to an in-memory store that is per-instance and
   lost on cold start — fine for offline dev, useless for a real admin page. NB:
   the KV secret `cosmos-connection-string` shipped as the literal `REPLACE-ME`
   placeholder; it was seeded with the real Cosmos primary connection string as
   part of CM-61 (this also un-broke `/api/ticket?code=` lookups).

```bash
# Set the two app settings on the prod SWA (operator action):
az staticwebapp appsettings set \
  --name swa-condomanager-prod \
  --setting-names \
    TENANT_ADMIN_ENABLED=1 \
    COSMOS_CONNECTION_STRING="$(az keyvault secret show \
      --vault-name kv-condomanager-prod \
      --name cosmos-connection-string --query value -o tsv)"

# Verify the route is registered AND enabled (expect a JSON array, HTTP 200):
curl -s -i "https://<SWA_URL>/api/tenants" | head -n 5
```

> ⚠️ **Security — unauthenticated PII.** `/api/tenants` has **no authentication**
> and serves tenant name/unit/mobile/email. The `TODO(auth)` markers in
> `portal/api/src/tenants.ts` are explicit: real auth (SWA roles / AAD) MUST land
> before exposing this on a public origin. Setting `TENANT_ADMIN_ENABLED=1` on a
> public prod SWA exposes tenant CRUD to anyone with the URL — acceptable only
> for a personal test environment with throwaway data. When real auth lands, gate
> `/api/tenants` via a mechanism that does **not** break SWA function forwarding
> (a `/api/admin/*`-style wildcard role rule bare-404s the path — see point 1).

---

## 12. Tear down / roll back

### 12.1 Delete individual resources (preferred — preserves others)

```bash
# Remove a single Cosmos container:
az cosmosdb sql container delete \
  --account-name cosmos-condomanager-dev \
  --resource-group rg-condomanager \
  --database-name condomanager \
  --name <container-name>

# Remove the entire Cosmos account (destructive — data loss):
az cosmosdb delete \
  --name cosmos-condomanager-dev \
  --resource-group rg-condomanager
```

### 12.2 Roll back a broken Bicep deploy

If a deploy leaves the RG in a broken state, re-deploy the last known-good
commit:

```bash
git checkout <last-known-good-sha> -- infra/
az deployment group create \
  --resource-group rg-condomanager \
  --template-file infra/bicep/main.bicep \
  --parameters infra/bicep/main.parameters.json \
  --parameters env=dev \
  --name "cm-rollback-$(date +%Y%m%d%H%M)"
```

Bicep is declarative — re-running the prior template converges the RG back to
the old desired state.

### 12.3 Full RG teardown (nuclear option)

Only use if you need a completely clean slate:

```bash
az group delete --name rg-condomanager --yes --no-wait
```

> **Warning:** This deletes dev AND prod resources (they share the RG). The Cosmos
> account is configured with **Periodic backup, 8-hour retention** (see
> `cosmos.bicep` `backupRetentionIntervalInHours: 8`) — data older than 8 hours is
> unrecoverable after an account deletion. Re-running the bootstrap (§1) and deploy
> (§2) will recreate the infrastructure from scratch, but existing data is gone
> permanently unless you opened an Azure support ticket to restore from a Periodic
> backup before the account was deleted.

### 12.4 Rollback checklist

- [ ] Identify the broken deployment in `az deployment group list --resource-group rg-condomanager`
- [ ] Re-run the prior template (§12.2) or revert the infra commit and let CI deploy
- [ ] Confirm all smoke tests pass (§5) on the recovered state
- [ ] Post a brief incident note in the relevant Jira story
