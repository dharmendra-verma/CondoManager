# CondoManager Infrastructure

This directory tracks the Azure infrastructure for the CondoManager platform.

## Topology — single shared Resource Group

CM-15 provisions **one** Resource Group (`rg-condomanager`) that hosts both
`dev` and `prod` workloads. The dev/prod distinction is applied at the
**resource** level (e.g. `cosmos-condomanager-dev` vs `cosmos-condomanager-prod`)
in later stories under Epic CM-1, not at the RG level.

This keeps management overhead and Azure cost low while still giving us two
deployment stages via GitHub Environments.

## Naming convention

| Resource type   | Pattern                       | Example                  |
|-----------------|-------------------------------|--------------------------|
| Resource Group  | `rg-condomanager` (shared)    | `rg-condomanager`        |
| Per-env resource| `<resource>-condomanager-<env>` | `cosmos-condomanager-dev`|
| Region          | Same region for all resources | `eastus2`                |

## Tagging convention (required)

Every resource provisioned by Bicep MUST carry these tags. The schema lives in
`infra/bicep/tags.bicep` (resource-group scope) and is inlined in
`infra/bicep/main.bicep` for the RG itself.

| Tag           | RG value          | Per-env resource value    |
|---------------|-------------------|---------------------------|
| `env`         | `shared`          | `dev` or `prod`           |
| `owner`       | `platform-team`   | `platform-team`           |
| `cost-center` | `cc-condomanager` | `cc-condomanager`         |
| `project`     | `condo-manager`   | `condo-manager`           |
| `managed-by`  | `bicep`           | `bicep`                   |

## Files

```
infra/
├── bicep/
│   ├── main.bicep                            # RG-scoped: orchestrates all modules
│   ├── tags.bicep                            # Reusable tag schema (env: dev|prod|shared)
│   ├── main.parameters.json                  # Single parameters file (env=dev today)
│   └── modules/                              # Per-resource Bicep modules
│       ├── vnet.bicep                        # VNet + /23 subnet delegated to Container Apps (CM-16)
│       ├── log-analytics.bicep               # Log Analytics workspace for app logs (CM-16)
│       ├── container-apps-env.bicep          # Container Apps Managed Environment (CM-16)
│       ├── container-app.bicep               # Hello-world Container App + MI attachment (CM-16, CM-18)
│       ├── cosmos.bicep                      # Cosmos DB account + db + 4 containers (CM-17)
│       ├── managed-identity.bicep            # User-Assigned MI shared by workloads (CM-18)
│       └── keyvault.bicep                    # Key Vault (RBAC) + MI role assignment (CM-18)
└── scripts/
    ├── cosmos-smoke-test.py                  # Post-deploy validation for Cosmos vector search (CM-17)
    ├── seed-keyvault-secrets.sh              # Seed the 8 initial secret names with REPLACE-ME (CM-18)
    └── keyvault-smoke-test.py                # Post-deploy validation: MI → KV read (CM-18)
.github/
└── workflows/
    ├── build.yml                             # PR + push:main · per-area lint/what-if + summary comment (CM-19)
    └── deploy.yml                            # push:main → deploy-dev, release:published → deploy-prod (CM-19)
tests/
└── infra/
    └── test_bicep_lint.sh                    # Lint test runs in CI on every PR
```

## How CI/CD works (CM-19)

Two workflows of single responsibility each. Detailed operator guide is in [`docs/CICD.md`](CICD.md).

| Event                                     | Workflow       | Job                       | Environment | Approval     |
|-------------------------------------------|----------------|---------------------------|-------------|--------------|
| `pull_request` (any path)                 | `build.yml`    | `detect` → `lint-infra` → `what-if-infra` → `summary` | _none_ | _none_ |
| `push:main` (touches `infra/`)            | `build.yml`    | `detect` → `lint-infra`   | _none_      | _none_       |
| `push:main` (touches `infra/`)            | `deploy.yml`   | `deploy-dev`              | `dev`       | none         |
| `release:published`                       | `deploy.yml`   | `deploy-prod`             | `prod`      | manual       |
| `workflow_dispatch (target_env=dev)`      | `deploy.yml`   | `deploy-dev`              | `dev`       | none         |
| `workflow_dispatch (target_env=prod)`     | `deploy.yml`   | `deploy-prod`             | `prod`      | manual       |

All Azure auth uses **OIDC federated credentials** — no long-lived secrets, no PATs.
The PR summary comment uses the built-in `GITHUB_TOKEN`. `marocchino/sticky-pull-request-comment@v2`
posts a single comment that updates in-place across PR pushes (header `ci-summary`).

### Why no staging?

The Jira AC for CM-19 mentioned a `tag → staging` deploy, but the project has only
**dev and prod** ([`CLAUDE.md §3`](../CLAUDE.md)). The mapping above implements the
spirit of the AC within the agreed topology: `push:main → dev`, `release:published → prod`.
The `workflow_dispatch` input is the escape hatch for any out-of-band re-deploy.

### Cutting a prod release

```bash
# 1. Tag the commit you want to ship
git tag -a v0.1.0 -m "Foundation phase: Container Apps + Cosmos + Key Vault wired"

# 2. Push the tag, then publish a release (this fires deploy.yml → deploy-prod)
git push origin v0.1.0
gh release create v0.1.0 --notes-from-tag
```

After publish, `deploy-prod` enters the `prod` environment and waits for an approver
before running `az deployment group create --parameters env=prod` against `rg-condomanager`.

## One-time setup (manual, by repo owner)

GitHub cannot create the Azure service principal for itself, and these steps
involve credentials that must never cross the chat:

```bash
# 1. Create an Azure AD application + service principal
az ad sp create-for-rbac --name "github-condomanager-infra" --skip-assignment

# 2. Grant the SP Contributor at the subscription scope
az role assignment create \
  --assignee <APP_ID> \
  --role Contributor \
  --scope /subscriptions/<SUBSCRIPTION_ID>

# 3. Configure federated credentials (OIDC) for GitHub — main branch
az ad app federated-credential create --id <APP_ID> --parameters '{
  "name": "github-main",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:dharmendra-verma/CondoManager:ref:refs/heads/main",
  "audiences": ["api://AzureADTokenExchange"]
}'

# 4. Federated credential for pull requests (what-if job)
az ad app federated-credential create --id <APP_ID> --parameters '{
  "name": "github-pr",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:dharmendra-verma/CondoManager:pull_request",
  "audiences": ["api://AzureADTokenExchange"]
}'
```

### Required GitHub secrets

In `Settings → Secrets and variables → Actions → New repository secret`:

| Secret name             | Source                                         |
|-------------------------|------------------------------------------------|
| `AZURE_CLIENT_ID`       | App registration `Application (client) ID`     |
| `AZURE_TENANT_ID`       | Azure AD tenant ID                             |
| `AZURE_SUBSCRIPTION_ID` | Target subscription ID                         |

### Required GitHub environments

In `Settings → Environments → New environment`:

| Environment | Used by                       | Reviewers required           |
|-------------|-------------------------------|------------------------------|
| `dev`       | future per-env resource jobs  | none                         |
| `prod`      | shared RG + per-env resources | at least one approver        |

## Deploying manually (smoke test)

The shared RG (`rg-condomanager`) is bootstrapped out-of-band — see
`infra/scripts/setup-azure-oidc.sh`. Once it exists, all subsequent
deployments are RG-scoped. `main.bicep` is resource-group scoped and
requires the `env` parameter (controls naming of per-env resources like
`cosmos-condomanager-<env>`):

```bash
az login
az account set --subscription <SUBSCRIPTION_ID>
az deployment group create \
  --resource-group rg-condomanager \
  --name cm-manual-$(date +%s) \
  --template-file infra/bicep/main.bicep \
  --parameters infra/bicep/main.parameters.json \
  --parameters env=dev
```

## Container Apps environment (CM-16)

```
VNet  vnet-condomanager-dev      10.0.0.0/16
 └── snet-containerapps-dev      10.0.0.0/23   (delegated to Microsoft.App/environments)

Log Analytics  law-condomanager-dev     PerGB2018, 30-day retention

Container Apps env  cae-condomanager-dev
 ├── Workload profile: Consumption  (free tier: 180K vCPU-sec/mo)
 ├── VNet integration: snet-containerapps-dev
 └── App logs: → law-condomanager-dev

Container App  ca-hello-condomanager-dev
 ├── Image:  mcr.microsoft.com/k8s/demo/hello-app:1.0
 ├── Resources: 0.25 vCPU / 0.5 Gi memory
 ├── Scale:  0–1 replicas (scale-to-zero when idle)
 └── Ingress: external, targetPort 8080
```

### Free-tier cost guard

- **Container Apps Consumption** grant: 180,000 vCPU-seconds and 400,000 GiB-seconds per month per subscription.
- The hello-world app uses 0.25 vCPU / 0.5 GiB with `minReplicas: 0`, so it consumes **zero vCPU-seconds while idle** and roughly 900 vCPU-sec per hour of continuous traffic — well under the monthly grant.
- **Log Analytics** grant: 5 GB ingestion per month, 31-day retention free. The hello-world generates minimal logs.

### Smoke test after deploy

After the CI deploy or a manual deploy lands:

```bash
# Grab the FQDN from the deployment outputs
FQDN=$(az deployment group show \
  --resource-group rg-condomanager \
  --name cm-manual \
  --query 'properties.outputs.containerAppFqdn.value' -o tsv)

# Curl it (first hit may take ~10s as the app scales from 0 to 1)
curl -i "https://${FQDN}/"
# expected: HTTP/2 200 + a "Hello, world!" body from mcr.microsoft.com/k8s/demo/hello-app
```

## Cosmos DB (CM-17)

A single Cosmos DB account per environment hosts both transactional data
and RAG vector embeddings.

| Aspect            | Value                                                            |
|-------------------|------------------------------------------------------------------|
| Account name      | `cosmos-condomanager-<env>`                                      |
| API               | NoSQL (Core SQL)                                                 |
| Capabilities      | `EnableNoSQLVectorSearch`                                        |
| Consistency       | Session                                                          |
| Free tier         | Enabled by default (25 GB + 1000 RU/s). One per subscription.    |
| Database          | `condomanager` — shared throughput, 1000 RU/s                    |
| Region            | `eastus2`                                                        |

### Containers

| Container         | Partition key  | Notes                                          |
|-------------------|----------------|------------------------------------------------|
| `tenants`         | `/id`          | One doc per tenant; point-reads by tenant ID   |
| `tickets`         | `/tenantId`    | Tenant-scoped ticket queries stay single-part. |
| `conversations`   | `/ticketId`    | All messages for a ticket live in one partition|
| `policies-vector` | `/tenantId`    | RAG embeddings + DiskANN index on `/embedding` |

### Vector search

`policies-vector` carries a `vectorEmbeddingPolicy` and a DiskANN
`vectorIndex` on `/embedding`. The raw embedding floats are excluded
from the standard index (saves RUs) — `VectorDistance()` SQL queries
hit the DiskANN index instead.

Default embedding dimensions: **1536** (matches OpenAI
`text-embedding-ada-002` and `text-embedding-3-small`). Switch to 3072
for `text-embedding-3-large` by overriding `cosmosVectorDimensions` in
`main.parameters.json`.

### Post-deploy smoke-test

`infra/scripts/cosmos-smoke-test.py` validates AC #4 ("Sample insert +
vector query validated"). Requires `azure-cosmos>=4.7.0`. Run AFTER
`az deployment group create` succeeds:

```bash
pip install "azure-cosmos>=4.7.0"

# Pull endpoint + key from the deployed account
COSMOS_ENDPOINT=$(az cosmosdb show \
  --resource-group rg-condomanager \
  --name cosmos-condomanager-dev \
  --query documentEndpoint -o tsv)
COSMOS_KEY=$(az cosmosdb keys list \
  --resource-group rg-condomanager \
  --name cosmos-condomanager-dev \
  --query primaryMasterKey -o tsv)

export COSMOS_ENDPOINT COSMOS_KEY
python infra/scripts/cosmos-smoke-test.py
```

The script inserts a dummy 1536-dim vector, queries it back with
`VectorDistance()`, asserts the inserted doc is the nearest neighbour,
and cleans up. Exit 0 means the account is wired correctly.

## Key Vault & Managed Identity (CM-18)

Key Vault is the single secret store. The only principal that can read
secret values is a User-Assigned Managed Identity attached to every
CondoManager workload.

| Aspect          | Value                                                       |
|-----------------|-------------------------------------------------------------|
| Vault name      | `kv-condomanager-<env>`                                     |
| Mode            | RBAC (no access policies)                                   |
| SKU             | Standard                                                    |
| Soft-delete     | 90 days                                                     |
| Purge protect.  | Enabled (irreversible — vault name reserved 90d after delete) |
| Public network  | `Enabled` for now; private endpoint in a later story        |
| MI name         | `id-condomanager-<env>` (User-Assigned, shared by all apps) |
| Role on vault   | `Key Vault Secrets User` (read-only on values)              |

User-Assigned (not System-Assigned) so the MI survives Container App
re-creation, can be RBAC-granted before any app exists, and is shared
by multiple workloads with a single role assignment per resource.

### Initial secret schema

`infra/bicep/modules/keyvault.bicep` declares the names (`secretNames`
param default); `infra/scripts/seed-keyvault-secrets.sh` populates each
with the literal placeholder `REPLACE-ME` so the schema exists. Real
values are NEVER written through IaC or the seed script — they're set
out-of-band by operators with `az keyvault secret set`. The lint test
diffs the two name lists to prevent drift.

| Secret name                 | Source                                              | Rotation                       |
|-----------------------------|-----------------------------------------------------|--------------------------------|
| `azure-openai-key`          | Azure OpenAI portal                                 | Every 90d; key1 ↔ key2         |
| `twilio-account-sid`        | Twilio console                                      | Rarely changes                 |
| `twilio-auth-token`         | Twilio console                                      | Quarterly                      |
| `twilio-whatsapp-number`    | Twilio console (phone number, not a secret per se)  | On reassignment                |
| `langsmith-api-key`         | LangSmith UI                                        | Quarterly                      |
| `langfuse-public-key`       | Langfuse UI (public, low-risk)                      | On project reset               |
| `langfuse-secret-key`       | Langfuse UI                                         | Quarterly                      |
| `cosmos-connection-string`  | `az cosmosdb keys list --type connection-strings`   | Primary ↔ secondary swap       |

### First-time setup after deploy

```bash
# 1. Seed the schema with REPLACE-ME placeholders
bash infra/scripts/seed-keyvault-secrets.sh kv-condomanager-dev

# 2. Replace placeholders with real values, one at a time
az keyvault secret set --vault-name kv-condomanager-dev \
    --name azure-openai-key --value "<real-value>"
# ... repeat for the other 7 secrets

# 3. Smoke-test that the MI can actually read a secret
pip install "azure-identity>=1.15.0" "azure-keyvault-secrets>=4.7.0"
python infra/scripts/keyvault-smoke-test.py --vault kv-condomanager-dev
```

The smoke-test uses `DefaultAzureCredential`, so it works both locally
(via `az login`) and from inside the Container App (via the attached
User-Assigned MI). Either succeeds once the role assignment has
propagated through Azure AD (usually under a minute).

### Rotation policy

- **Quarterly cadence** for app credentials (Twilio, LangSmith, Langfuse).
  Calendar reminder is owned by `platform-team`.
- **On-demand** for Azure OpenAI and Cosmos: rotate using the key1 → key2
  swap pattern (update the KV secret to key2, regenerate key1, then
  optionally swap back next rotation).
- **Never delete** an old secret version — Key Vault keeps history, and
  running Container App revisions may still reference older versions
  until they're replaced.
- **Always smoke-test after a rotation** with `keyvault-smoke-test.py`.

### Why values aren't in IaC

A Bicep `Microsoft.KeyVault/vaults/secrets` resource requires a
`value` property. That value would then live in source, in deployment
history, and in any exported ARM template — none acceptable. So Bicep
declares the vault + the MI binding only; values come from out-of-band
`az keyvault secret set` calls (manual today, possibly an Azure DevOps
release pipeline later, but never from this repo).

## Adding per-env resources in later stories

Each new resource type gets its own module under `infra/bicep/modules/`,
following the CM-16 / CM-17 / CM-18 pattern: accept `env`, `location`,
and `tags` params, emit any resource IDs downstream modules need as
outputs, and let `main.bicep` chain them in dependency order.
