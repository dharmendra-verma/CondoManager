// main.bicep — entry point for resources deployed INTO rg-condomanager.
// Jira: CM-15 (RG + scoped SP)  | CM-16 (Container Apps env)  | CM-17 (Cosmos DB)
//       CM-18 (Key Vault + User-Assigned MI)  | CM-20 (ACR + base image)
//       CM-22 (Application Insights as OTLP backend)  | CM-23 (LangSmith tracing)
//       CM-25 (Operations workbook over App Insights)
// Epic: CM-1 (Foundation & Azure Infrastructure)  | Phase 0
//
// The shared resource group (rg-condomanager) is pre-created as a one-time
// bootstrap (see infra/scripts/setup-azure-oidc.sh + docs/INFRA.md) so that
// the CI service principal holds Contributor on the RG only — never on the
// subscription. That keeps blast-radius scoped to a single RG.
//
// Per-env resources live in this single RG and are named
// `<resource>-condomanager-<env>` (e.g. cosmos-condomanager-dev,
// cae-condomanager-dev). Tags on each downstream resource come from
// tags.bicep so the schema stays consistent.
//
// Deployment (dev):
//   az deployment group create \
//     --resource-group rg-condomanager \
//     --template-file infra/bicep/main.bicep \
//     --parameters infra/bicep/main.parameters.json \
//     --parameters env=dev

targetScope = 'resourceGroup'

@description('Deployment environment. Drives per-resource naming (cosmos-condomanager-<env>, cae-condomanager-<env>, …).')
@allowed([ 'dev', 'prod' ])
param env string = 'dev'

@description('Azure region. Defaults to the resource group location so dev and prod stay co-located.')
param location string = resourceGroup().location

@description('Enable the Cosmos DB free tier. Only ONE free-tier account is allowed per subscription — set to false for prod if the subscription already has one.')
param cosmosEnableFreeTier bool = true

@description('Embedding vector dimensions for the policies-vector container. 1536 matches OpenAI text-embedding-ada-002 / text-embedding-3-small.')
@minValue(2)
@maxValue(4096)
param cosmosVectorDimensions int = 1536

@description('Server-side adaptive sampling percentage on the Application Insights resource (CM-22). 100 = no drop; lower values reduce ingestion cost. Dev defaults to 100, prod to 50; tune per env without code change.')
@minValue(0)
@maxValue(100)
param appInsightsSamplingPercentage int = env == 'dev' ? 100 : 50

@description('Enable LangSmith tracing on the Container App (CM-23). Defaults to enabled in dev, disabled in prod (CM-24 wires Langfuse for prod LLM observations; running both would double-pay and emit twice per call).')
param langsmithEnabled bool = env == 'dev'

@description('LangSmith ingestion endpoint. US default; override to https://eu.api.smith.langchain.com for EU customers.')
param langsmithEndpoint string = 'https://api.smith.langchain.com'

// Tag schema — every downstream resource carries the same five tags.
module tagsModule './tags.bicep' = {
  name: 'tags-${env}'
  params: {
    env: env
    costCenter: 'cc-condomanager'
  }
}

// VNet + delegated /23 subnet for Container Apps. (CM-16)
module vnet './modules/vnet.bicep' = {
  name: 'vnet-${env}'
  params: {
    env: env
    location: location
    tags: tagsModule.outputs.tags
  }
}

// Log Analytics workspace — required by Container Apps appLogsConfiguration. (CM-16)
module logAnalytics './modules/log-analytics.bicep' = {
  name: 'law-${env}'
  params: {
    env: env
    location: location
    tags: tagsModule.outputs.tags
  }
}

// Application Insights (workspace-based) — OTLP backend for OTel spans. (CM-22)
// Linked to the LAW above; the Python side (configure_otel) routes spans here
// when APPLICATIONINSIGHTS_CONNECTION_STRING is set on the workload. The
// connection string is surfaced from main as a @secure output; the post-deploy
// `seed-app-insights-secret.sh` populates the KV secret from that output.
module appInsights './modules/app-insights.bicep' = {
  name: 'appi-${env}'
  params: {
    env: env
    location: location
    tags: tagsModule.outputs.tags
    logAnalyticsWorkspaceId: logAnalytics.outputs.workspaceId
    samplingPercentage: appInsightsSamplingPercentage
  }
}

// Operations workbook over App Insights — 4 panels (cost / latency /
// errors / HITL queue depth). Built now to lock the OTel attribute
// schema; panels render empty until CM-28+ ships agent traffic. (CM-25)
module workbook './modules/workbook.bicep' = {
  name: 'wb-${env}'
  params: {
    env: env
    location: location
    tags: tagsModule.outputs.tags
    appInsightsId: appInsights.outputs.appInsightsId
  }
}

// Container Apps Managed Environment (Consumption, VNet-integrated). (CM-16)
module containerAppsEnv './modules/container-apps-env.bicep' = {
  name: 'cae-${env}'
  params: {
    env: env
    location: location
    tags: tagsModule.outputs.tags
    infrastructureSubnetId: vnet.outputs.containerAppsSubnetId
    logAnalyticsCustomerId: logAnalytics.outputs.workspaceCustomerId
    logAnalyticsSharedKey: logAnalytics.outputs.workspaceSharedKey
  }
}

// User-Assigned Managed Identity shared by all CondoManager workloads. (CM-18)
// Provisioned before the Container App so the MI ID can be passed in at
// app-creation time (avoids a second deployment to attach the identity).
module managedIdentity './modules/managed-identity.bicep' = {
  name: 'mi-${env}'
  params: {
    env: env
    location: location
    tags: tagsModule.outputs.tags
  }
}

// Key Vault (RBAC mode) + role assignment of the shared MI to
// `Key Vault Secrets User`. (CM-18)
module keyvault './modules/keyvault.bicep' = {
  name: 'kv-${env}'
  params: {
    env: env
    location: location
    tags: tagsModule.outputs.tags
    managedIdentityPrincipalId: managedIdentity.outputs.principalId
  }
}

// KV secret URIs for Container Apps' native secretRef integration. The MI
// resolves these against KV at revision start. `vaultUri` ends with `/`, so
// the concatenation produces the canonical
// `https://<kv-name>.vault.azure.net/secrets/<secret-name>` form.
var appInsightsKvSecretUri = '${keyvault.outputs.vaultUri}secrets/app-insights-connection-string'
var langsmithKvSecretUri = '${keyvault.outputs.vaultUri}secrets/langsmith-api-key'

// CM-23: project name routed via LANGCHAIN_PROJECT. One LangSmith project per
// env so traces don't bleed between dev iteration and any prod opt-in.
var langsmithProject = 'condomanager-${env}'

// Hello-world Container App — smoke-test surface for CM-16 AC.
// CM-18: attaches the shared User-Assigned MI so the app can read KV secrets
// via DefaultAzureCredential once placeholder values are populated.
// CM-22: mounts the App Insights connection string from KV as an env var so
// configure_otel() switches to the Azure Monitor exporter automatically once
// the post-deploy seed step replaces the REPLACE-ME placeholder.
// CM-23: mounts the LangSmith API key + sets LANGCHAIN_* env vars when
// langsmithEnabled is true (default: dev only — prod is reserved for Langfuse
// in CM-24).
module containerApp './modules/container-app.bicep' = {
  name: 'ca-hello-${env}'
  params: {
    env: env
    location: location
    tags: tagsModule.outputs.tags
    environmentId: containerAppsEnv.outputs.environmentId
    userAssignedIdentityId: managedIdentity.outputs.identityId
    appInsightsKvSecretUri: appInsightsKvSecretUri
    langsmithKvSecretUri: langsmithEnabled ? langsmithKvSecretUri : ''
    langsmithProjectName: langsmithEnabled ? langsmithProject : ''
    langsmithEndpoint: langsmithEndpoint
  }
}

// Cosmos DB account + condomanager database + 4 containers (tenants,
// tickets, conversations, policies-vector with DiskANN vector search). (CM-17)
module cosmos './modules/cosmos.bicep' = {
  name: 'cosmos-${env}'
  params: {
    env: env
    location: location
    tags: tagsModule.outputs.tags
    enableFreeTier: cosmosEnableFreeTier
    vectorDimensions: cosmosVectorDimensions
  }
}

// Azure Container Registry (Basic SKU) — destination for the curated Python
// base image built by .github/workflows/base-image.yml. (CM-20)
// The hello-world Container App still pulls from MCR; ACR's first consumers
// are the future agent-runtime stories that FROM acrcondomanager<env>.azurecr.io/base/python:3.12-slim-latest.
module acr './modules/acr.bicep' = {
  name: 'acr-${env}'
  params: {
    env: env
    location: location
    tags: tagsModule.outputs.tags
  }
}

// Resource group identity outputs (kept from CM-15 for OIDC + scope smoke test).
output resourceGroupId string = resourceGroup().id
output resourceGroupName string = resourceGroup().name
output resourceGroupLocation string = resourceGroup().location
output resourceGroupTags object = resourceGroup().tags

// CM-16 outputs — used by the smoke test to curl the hello-world app.
output vnetName string = vnet.outputs.vnetName
output logAnalyticsWorkspaceName string = logAnalytics.outputs.workspaceName
output containerAppsEnvironmentName string = containerAppsEnv.outputs.environmentName
output containerAppsEnvironmentDefaultDomain string = containerAppsEnv.outputs.defaultDomain
output containerAppName string = containerApp.outputs.containerAppName
output containerAppFqdn string = containerApp.outputs.fqdn

// CM-17 outputs — surface Cosmos endpoint for the post-deploy smoke-test
// (infra/scripts/cosmos-smoke-test.py).
output cosmosAccountName string = cosmos.outputs.accountName
output cosmosEndpoint string = cosmos.outputs.endpoint
output cosmosDatabaseName string = cosmos.outputs.databaseName

// CM-18 outputs — used by seed-keyvault-secrets.sh + keyvault-smoke-test.py.
output managedIdentityName string = managedIdentity.outputs.identityName
output managedIdentityClientId string = managedIdentity.outputs.clientId
output keyVaultName string = keyvault.outputs.vaultName
output keyVaultUri string = keyvault.outputs.vaultUri

// CM-20 outputs — used by .github/workflows/base-image.yml + future app stories.
output acrName string = acr.outputs.acrName
output acrLoginServer string = acr.outputs.loginServer

// CM-22 outputs — App Insights name + ID for tooling discovery, and the
// connection string for the post-deploy `seed-app-insights-secret.sh` to
// write into KV. @secure() keeps the conn string out of deployment-history
// plaintext (it carries an InstrumentationKey which is functionally a token).
output appInsightsName string = appInsights.outputs.appInsightsName
output appInsightsId string = appInsights.outputs.appInsightsId
@secure()
output appInsightsConnectionString string = appInsights.outputs.connectionString

// CM-25 outputs — workbook resource ID surfaces for operator pin-to-dashboard
// + tooling discovery (the deep-link to the workbook builds from the ID).
output workbookId string = workbook.outputs.workbookId
output workbookName string = workbook.outputs.workbookName

// CM-23 outputs — empty string when LangSmith is disabled, lets tooling
// (e.g. seed-langsmith-dataset.py) discover the project name without
// hard-coding the convention.
output langsmithProject string = langsmithEnabled ? langsmithProject : ''
output langsmithEnabled bool = langsmithEnabled
