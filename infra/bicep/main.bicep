// main.bicep — entry point for resources deployed INTO rg-condomanager.
// Jira: CM-15 (RG + scoped SP)  | CM-16 (Container Apps env)  | CM-17 (Cosmos DB)
//       CM-18 (Key Vault + User-Assigned MI)  | CM-20 (ACR + base image)
//       CM-22 (Application Insights as OTLP backend)  | CM-23 (LangSmith tracing)
//       CM-25 (Operations workbook over App Insights)  | CM-26 (Action Group + Budget + Alert rules)
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

@description('Enable the Cosmos DB free tier. Only ONE free-tier account is allowed per subscription. This param only takes effect for env=dev; prod always deploys with the free tier OFF (see the cosmos module wiring below) because dev already claims the one allowed free-tier account.')
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

@description('Monthly Azure spend budget in USD for the resource group (CM-26). Triggers alerts at 50/80/100% Actual. Dev defaults to 100, prod to 500 — operator tunes after the first month of real usage data lands.')
@minValue(1)
param alertMonthlyBudgetUsd int = env == 'dev' ? 100 : 500

@description('Slack incoming webhook URL for the Action Group webhook receiver (CM-26). Empty string omits the webhook entirely; operator fills via `--parameters alertSlackWebhookUrl=...` at deploy time OR Azure Portal post-deploy. @secure() keeps the URL out of deployment-history plaintext.')
@secure()
param alertSlackWebhookUrl string = ''

@description('Email recipient for the Action Group email receiver (CM-26). Empty string omits the email entirely. Plain string (not @secure) since email addresses are not credentials.')
param alertEmail string = ''

@description('Minimum LLM call count in the 1h window before the hallucination-spike alert can fire (CM-26). Default 10 catches the "zero traffic = zero refusal = panic" false positive in phase 0; raise once steady-state prod LLM traffic is known.')
@minValue(1)
param hallucinationSpikeMinCalls int = 10

@description('Google Drive folder id the CM-34 sync job watches. Empty by default; an operator supplies it post-deploy (the function skips cleanly until then). Not a secret — a folder id is not sensitive.')
param gdriveFolderId string = ''

@description('Tenant id the CM-34 Drive sync attributes ingested policy docs to (Cosmos /tenantId partition).')
param gdriveTenantId string = 'default'

@description('Azure OpenAI endpoint for CM-34 embeddings. Empty until an Azure OpenAI resource is provisioned in a later story; the sync function skips cleanly when unset.')
param azureOpenAiEndpoint string = ''

@description('Deploy the CM-34 Google Drive → Cosmos knowledge-sync Function App (gdrive-sync). Defaults to dev-only: prod skips it so a prod deploy is not blocked by the App Service Plan VM quota (see CM-58). Override to true for prod once the quota is granted. Disabling only skips the timer-driven sync job; the core message pipeline is unaffected.')
param deployGdriveSync bool = env == 'dev'

@description('Deploy the CM-36 weekly Analytics & Forecasting digest Function App (analytics-digest). Defaults to dev-only for the same reason as deployGdriveSync (CM-58 App Service Plan quota). Override to true for prod once the quota is granted.')
param deployAnalytics bool = env == 'dev'

@description('Fully-qualified agent-runtime image to run on the Container App (CM-59), e.g. acrcondomanager<env>.azurecr.io/agent:<git-sha>. Empty (default) keeps the public hello-world placeholder image — so an infra-only deploy (or any deploy that does not first build the image) stays green. deploy.yml builds the image via `az acr build` and passes this param. When set, the Container App switches to port 8000, authenticates the pull via the MI (AcrPull, acr-rbac.bicep), and enables the CM-55 web-chat channel (WEBCHAT_TEST_ENABLED=1).')
param agentImage string = ''

// CM-59: a real agent image flips the Container App from the public hello-world
// shell (port 80, anonymous MCR pull, channel off) to the agent runtime (port
// 8000, private ACR pull via the MI, web-chat channel on). Computed once here so
// the three coupled settings can't drift apart.
var hasAgentImage = !empty(agentImage)
var containerImage = hasAgentImage ? agentImage : 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
var containerTargetPort = hasAgentImage ? 8000 : 80

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

// CM-26 — One shared Action Group fed by the Budget thresholds and all
// three scheduled-query alert rules. Email + Slack receivers are
// conditional on operator-supplied params; empty deploy is legal
// (alerts fire, page nobody, sit in the Azure Monitor "fired" list).
module actionGroup './modules/action-group.bicep' = {
  name: 'ag-${env}'
  params: {
    env: env
    tags: tagsModule.outputs.tags
    emailAddress: alertEmail
    slackWebhookUrl: alertSlackWebhookUrl
  }
}

// CM-26 — Azure Consumption Budget with 50/80/100% Actual thresholds.
// RG-scoped because no Microsoft.CognitiveServices/accounts (Azure
// OpenAI) resource exists yet; when CM-OpenAI lands OpenAI dominates
// RG spend and the operator can tighten the filter to that resource.
module budget './modules/budget.bicep' = {
  name: 'budget-${env}'
  params: {
    env: env
    actionGroupId: actionGroup.outputs.actionGroupId
    monthlyAmountUsd: alertMonthlyBudgetUsd
  }
}

// CM-26 — Three scheduled-query alert rules over App Insights:
// latency SLO breach (p95 > 2s), guardrail trip (cost or loop cap),
// hallucination spike (refusal-rate floor with traffic gate).
module alertRules './modules/alert-rules.bicep' = {
  name: 'alerts-${env}'
  params: {
    env: env
    location: location
    tags: tagsModule.outputs.tags
    appInsightsId: appInsights.outputs.appInsightsId
    actionGroupId: actionGroup.outputs.actionGroupId
    hallucinationSpikeMinCalls: hallucinationSpikeMinCalls
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
    // CM-57: seed the app-insights-connection-string secret at deploy time from
    // the App Insights output so the Container App's secretRef resolves on a
    // fresh deploy. The container app already depends on this module (via
    // vaultUri), so creating the secret here guarantees it exists before the app
    // provisions. Supersedes the chicken-and-egg post-deploy seed for this one
    // secret (seed-app-insights-secret.sh stays valid for rotation/re-seed).
    appInsightsConnectionString: appInsights.outputs.connectionString
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
    // AZURE_CLIENT_ID for in-app DefaultAzureCredential (web-chat Cosmos lookup).
    // Required for the user-assigned MI — without it ManagedIdentityCredential
    // can't resolve which identity to use. See CM-62.
    managedIdentityClientId: managedIdentity.outputs.clientId
    appInsightsKvSecretUri: appInsightsKvSecretUri
    langsmithKvSecretUri: langsmithEnabled ? langsmithKvSecretUri : ''
    langsmithProjectName: langsmithEnabled ? langsmithProject : ''
    langsmithEndpoint: langsmithEndpoint
    // CM-59: real agent image (or the hello-world default) + its coupled port,
    // private-pull registry, and channel flag.
    image: containerImage
    targetPort: containerTargetPort
    registryServer: hasAgentImage ? acr.outputs.loginServer : ''
    webchatEnabled: hasAgentImage
    // CM-60: allow the prod portal (Static Web App) origin to call /web/* from
    // the browser. Creates an implicit dependency on the staticWebApp module.
    webchatCorsOrigins: hasAgentImage ? 'https://${staticWebApp.outputs.defaultHostname}' : ''
    // CM-55 follow-up: let the web-chat login resolve against the live `tenants`
    // Cosmos container (the MI already holds Cosmos data-plane RBAC via
    // cosmosRbac). Only wired when the real agent image runs the channel.
    cosmosEndpoint: hasAgentImage ? cosmos.outputs.endpoint : ''
  }
  // The MI's AcrPull grant must exist before the revision pulls the private
  // image at start. dependsOn is unconditional (acrRbac always deploys); it is
  // a harmless ordering edge when the hello-world image is in use.
  dependsOn: [ acrRbac ]
}

// Cosmos DB account + condomanager database + 4 containers (tenants,
// tickets, conversations, policies-vector with DiskANN vector search). (CM-17)
module cosmos './modules/cosmos.bicep' = {
  name: 'cosmos-${env}'
  params: {
    env: env
    location: location
    tags: tagsModule.outputs.tags
    // CM-57: Azure allows only ONE free-tier Cosmos account per subscription.
    // dev already holds it, so prod MUST request enableFreeTier=false or the
    // deploy fails with "Free tier has already been applied to another Azure
    // Cosmos DB account in this subscription". Force it off for prod regardless
    // of the cosmosEnableFreeTier param (which main.parameters.json pins to true
    // for the dev default) — the param only takes effect in dev.
    enableFreeTier: cosmosEnableFreeTier && env == 'dev'
    vectorDimensions: cosmosVectorDimensions
  }
}

// CM-38 — Cosmos data-plane RBAC. Grants the shared MI the built-in Cosmos
// "Data Contributor" role (scoped to the condomanager database) so workloads
// authenticate to Cosmos via AAD/DefaultAzureCredential rather than account
// keys. This is the Azure half of AC3; field-level restriction is enforced in
// app code (agents/security/field_access.py). Depends on cosmos + managedIdentity.
module cosmosRbac './modules/cosmos-rbac.bicep' = {
  name: 'cosmos-rbac-${env}'
  params: {
    env: env
    principalId: managedIdentity.outputs.principalId
  }
  // Explicit dependency: the role assignment references the account by name via
  // `existing`, so Bicep can't infer it must wait for cosmos.bicep to create it.
  dependsOn: [ cosmos ]
}

// CM-34 — Google Drive → Cosmos vector sync job (Azure Functions Timer).
// Linux Consumption Python 3.12 Function App + dedicated storage. Attached to
// the shared MI for Cosmos data-plane access + Key Vault reference resolution;
// secrets (google-drive-sa-key, azure-openai-key) mount as KV references.
// Depends (implicitly, via passed outputs) on cosmos + keyvault + managed
// identity + app insights.
// CM-58: gated on deployGdriveSync (dev-only by default) so prod is not blocked
// by the App Service Plan VM quota until that quota is granted.
module functions './modules/functions.bicep' = if (deployGdriveSync) {
  name: 'functions-${env}'
  params: {
    env: env
    location: location
    tags: tagsModule.outputs.tags
    userAssignedIdentityId: managedIdentity.outputs.identityId
    appInsightsConnectionString: appInsights.outputs.connectionString
    cosmosEndpoint: cosmos.outputs.endpoint
    keyVaultUri: keyvault.outputs.vaultUri
    gdriveFolderId: gdriveFolderId
    gdriveTenantId: gdriveTenantId
    azureOpenAiEndpoint: azureOpenAiEndpoint
  }
}

// CM-36 — weekly Analytics & Forecasting digest job (Azure Functions Timer).
// Dedicated Linux Consumption Function App (separate from gdrive-sync) so the
// weekly schedule + failures are isolated. Reads tickets + escalations from
// Cosmos via the shared MI; posts the digest to the slack-webhook-url KV ref.
// CM-58: gated on deployAnalytics (dev-only by default) — same App Service Plan
// quota reason as the gdrive-sync app above.
module analytics './modules/analytics.bicep' = if (deployAnalytics) {
  name: 'analytics-${env}'
  params: {
    env: env
    location: location
    tags: tagsModule.outputs.tags
    userAssignedIdentityId: managedIdentity.outputs.identityId
    appInsightsConnectionString: appInsights.outputs.connectionString
    cosmosEndpoint: cosmos.outputs.endpoint
    keyVaultUri: keyvault.outputs.vaultUri
  }
}

// CM-37 — Tenant status portal (read-only) on Azure Static Web Apps (Free).
// Static SPA + managed Functions API (GET /api/ticket). No repo linkage —
// code deploys via the SWA deployment token from deploy.yml; the API reads
// Cosmos via a COSMOS_CONNECTION_STRING app setting seeded post-deploy.
module staticWebApp './modules/static-web-app.bicep' = {
  name: 'swa-${env}'
  params: {
    env: env
    location: location
    tags: tagsModule.outputs.tags
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

// CM-59 — grant the shared MI AcrPull on the registry so the agent-runtime
// Container App can pull its private image (acrcondomanager<env>.azurecr.io/
// agent:<tag>). CM-20 created the registry but deferred this grant. Wired
// unconditionally (harmless while the hello-world image is in use); the
// Container App dependsOn this so the role exists before the first pull.
module acrRbac './modules/acr-rbac.bicep' = {
  name: 'acr-rbac-${env}'
  params: {
    env: env
    principalId: managedIdentity.outputs.principalId
  }
  // The role assignment references the registry by name via `existing`, so Bicep
  // can't infer it must wait for acr.bicep to create it.
  dependsOn: [ acr ]
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

// CM-26 outputs — Action Group + budget + alert rule names. Surface
// the rule names as an object so tooling (Azure Portal deep-links,
// future SDK probes) can discover them without depending on the naming
// convention.
output actionGroupId string = actionGroup.outputs.actionGroupId
output budgetName string = budget.outputs.budgetName
output alertRuleNames object = {
  latencySlo: alertRules.outputs.latencySloRuleName
  guardrailTrip: alertRules.outputs.guardrailTripRuleName
  hallucinationSpike: alertRules.outputs.hallucinationSpikeRuleName
}

// CM-23 outputs — empty string when LangSmith is disabled, lets tooling
// (e.g. seed-langsmith-dataset.py) discover the project name without
// hard-coding the convention.
output langsmithProject string = langsmithEnabled ? langsmithProject : ''
output langsmithEnabled bool = langsmithEnabled

// CM-34 outputs — Function App identity for the post-deploy `func publish`
// step + the gdrive-sync smoke test discovery. Empty string when the app is not
// deployed (CM-58: deployGdriveSync=false), so tooling can detect "not deployed".
// `.?` safe-dereferences the conditional module (null when not deployed) and
// `?? ''` collapses that to an empty string — avoids BCP318 on the access.
output functionAppName string = functions.?outputs.functionAppName ?? ''
output functionAppId string = functions.?outputs.functionAppId ?? ''
output functionAppDefaultHostName string = functions.?outputs.functionAppDefaultHostName ?? ''

// CM-36 — analytics digest Function App (used by the deploy step + smoke test).
// Empty string when not deployed (CM-58: deployAnalytics=false).
output analyticsFunctionAppName string = analytics.?outputs.functionAppName ?? ''
output analyticsFunctionAppId string = analytics.?outputs.functionAppId ?? ''

// CM-37 outputs — SWA name + hostname for the post-deploy token wiring +
// the tenant-facing URL.
output staticWebAppName string = staticWebApp.outputs.staticWebAppName
output staticWebAppDefaultHostname string = staticWebApp.outputs.defaultHostname

// CM-38 — Cosmos data-plane RBAC assignment name (tooling discovery).
output cosmosDataPlaneRoleAssignmentName string = cosmosRbac.outputs.roleAssignmentName
