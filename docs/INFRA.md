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
└── bicep/
    ├── main.bicep                            # RG-scoped: orchestrates all modules
    ├── tags.bicep                            # Reusable tag schema (env: dev|prod|shared)
    ├── main.parameters.json                  # Single parameters file (env=dev today)
    └── modules/                              # Per-resource Bicep modules (CM-16+)
        ├── vnet.bicep                        # VNet + /23 subnet delegated to Container Apps
        ├── log-analytics.bicep               # Log Analytics workspace for app logs
        ├── container-apps-env.bicep          # Container Apps Managed Environment (Consumption)
        └── container-app.bicep               # Hello-world Container App (smoke-test surface)
.github/
└── workflows/
    └── infra-deploy.yml       # CI: lint → what-if → deploy (single RG)
tests/
└── infra/
    └── test_bicep_lint.sh     # Lint test runs in CI on every PR
```

## How CI works

1. **PR opened touching `infra/`** → `lint` + `what-if` (no apply).
2. **Push to `main` touching `infra/`** → `lint` + `deploy-rg`. The `deploy-rg`
   job is gated by the `prod` GitHub Environment (manual approval) because
   the shared RG is a production resource.
3. All deployments use **OIDC federated credentials** (no long-lived secrets).

When per-env resource stories (Cosmos DB, Key Vault, ACR, Container Apps env)
land, their workflows will add `deploy-dev` and `deploy-prod` jobs gated by
the `dev` and `prod` GitHub Environments respectively.

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
deployments are RG-scoped:

```bash
az login
az account set --subscription <SUBSCRIPTION_ID>
az deployment group create \
  --resource-group rg-condomanager \
  --name cm-manual \
  --template-file infra/bicep/main.bicep \
  --parameters infra/bicep/main.parameters.json
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

## Adding per-env resources in later stories

Each new resource type gets its own module under `infra/bicep/modules/`,
following the CM-16 pattern: accept `env`, `location`, and `tags` params,
emit any resource IDs that downstream modules need as outputs, and let
`main.bicep` chain them in dependency order.

```bicep
// example: cosmos.bicep (CM-17, illustrative)
targetScope = 'resourceGroup'

@allowed([ 'dev', 'prod' ])
param env string
param location string
param tags object

resource cosmosDev 'Microsoft.DocumentDB/databaseAccounts@2024-08-15' = {
  name: 'cosmos-condomanager-${env}'
  location: location
  tags: tags
  // ...
}
```

Then wire it into `main.bicep`:

```bicep
module cosmos './modules/cosmos.bicep' = {
  name: 'cosmos-${env}'
  params: {
    env: env
    location: location
    tags: tagsModule.outputs.tags
  }
}
```
