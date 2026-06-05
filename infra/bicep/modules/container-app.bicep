// container-app.bicep — Hello-world Container App (smoke-test surface).
// Jira: CM-16 (initial)  | CM-18 (User-Assigned MI)  | CM-22 (App Insights via KV secretRef)
//       CM-23 (LangSmith tracing env vars + KV secretRef)
// Epic: CM-1 (Foundation & Azure Infrastructure)  | Phase 0
//
// Acts as the initial app shell so CM-16 has something to deploy. The image
// is Microsoft's official Container Apps quickstart hello-world, pulled from
// MCR (no ACR yet). Scale 0-1 + 0.25 vCPU / 0.5 Gi keeps idle cost at zero
// and stays well inside the 180K vCPU-sec/mo Consumption free grant even
// if poked frequently.
//
// External ingress + targetPort 80 (the port mcr.microsoft.com/azuredocs/
// containerapps-helloworld serves on). transport `auto` lets the platform
// pick HTTP/1.1 vs HTTP/2 based on the client.
//
// CM-18: When `userAssignedIdentityId` is supplied, the app attaches that MI
// and can read Key Vault secrets via DefaultAzureCredential. An empty string
// (the default) keeps backward-compat for any caller that doesn't pass the
// param — the `identity` block is omitted entirely in that case.
//
// CM-22: When `appInsightsKvSecretUri` is supplied (alongside the MI), the
// platform pulls the App Insights connection string from KV at revision
// start via `secretRef` and exposes it as APPLICATIONINSIGHTS_CONNECTION_STRING
// on the container. Python's `configure_otel` reads that env var and switches
// to the Azure Monitor exporter. Empty string omits both `secrets[]` and the
// env var — back-compat for callers that don't wire App Insights.
//
// CM-24: Langfuse production keys (`langfuse-public-key`, `langfuse-secret-key`)
// will follow the same KV→secretRef pattern as APPLICATIONINSIGHTS_CONNECTION_STRING
// once CM-26 wires the chain. Future env vars are LANGFUSE_PUBLIC_KEY,
// LANGFUSE_SECRET_KEY, and LANGFUSE_HOST (literal `https://cloud.langfuse.com`).
// Until then, `agents/observability/langfuse_export.py` reads them from
// `os.environ`; both keys unset (the default) means Langfuse stays disabled
// — see is_langfuse_enabled() in that module.

targetScope = 'resourceGroup'

@description('Environment short name (dev or prod). Used in resource names and tags.')
@allowed([ 'dev', 'prod' ])
param env string

@description('Azure region. Passthrough from main.bicep.')
param location string

@description('Tag map produced by tags.bicep.')
param tags object

@description('Resource ID of the parent Container Apps Managed Environment.')
param environmentId string

@description('Hello-world image. Microsoft\'s official Container Apps quickstart sample on MCR (no auth needed); will move to ACR with CM-18.')
param image string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Port the container listens on. The containerapps-helloworld image serves on 80.')
param targetPort int = 80

@description('CPU cores allocated to the container. 0.25 is the Consumption-plan minimum.')
param cpu string = '0.25'

@description('Memory allocated to the container. Must be paired with cpu per the Consumption sizing table.')
param memory string = '0.5Gi'

@description('Minimum replica count. 0 means scale-to-zero when idle (no vCPU-sec spend).')
@minValue(0)
param minReplicas int = 0

@description('Maximum replica count. Capped at 1 for the hello-world smoke test.')
@minValue(1)
param maxReplicas int = 1

@description('Resource ID of a User-Assigned Managed Identity to attach. Empty string (default) omits the identity block entirely for backward-compat.')
param userAssignedIdentityId string = ''

@description('Key Vault secret URI (https://<vault>.vault.azure.net/secrets/<name>) for the App Insights connection string. Empty string (default) omits the App Insights env var entirely — back-compat for callers that do not wire App Insights. Requires userAssignedIdentityId to also be set, since the MI is what resolves the secretRef against KV.')
param appInsightsKvSecretUri string = ''

@description('Key Vault secret URI for the LangSmith API key (CM-23). Empty string (default) omits LangSmith env vars. Requires userAssignedIdentityId and a non-empty langsmithProjectName.')
param langsmithKvSecretUri string = ''

@description('LangSmith project name routed via LANGCHAIN_PROJECT (CM-23). Empty string omits LangSmith env vars even if the secret URI is set.')
param langsmithProjectName string = ''

@description('LangSmith ingestion endpoint. US default; override to https://eu.api.smith.langchain.com for EU.')
param langsmithEndpoint string = 'https://api.smith.langchain.com'

@description('ACR login server (e.g. acrcondomanager<env>.azurecr.io) for a PRIVATE image pull (CM-59). Empty string (default) omits the registries block — back-compat for the public hello-world image, which needs no auth. When set, requires userAssignedIdentityId: the MI authenticates the pull and must hold AcrPull (see acr-rbac.bicep).')
param registryServer string = ''

@description('When true, sets WEBCHAT_TEST_ENABLED=1 so the CM-55 web-chat channel is live — the prod inbound entry point (CM-59). Default false keeps the endpoints 404 ("not deployed") for the hello-world shell and any caller that does not opt in.')
param webchatEnabled bool = false

@description('Comma-separated extra CORS origins for the web-chat app (CM-60), set as WEBCHAT_CORS_ORIGINS so the prod portal (Static Web App) — a different origin than this Container App — can call /web/* from the browser. Empty (default) leaves only the localhost dev origins the app hardcodes. Only emitted when webchatEnabled is true.')
param webchatCorsOrigins string = ''

@description('Cosmos DB account endpoint (CM-17). Emitted as COSMOS_ENDPOINT so the web-chat tenant directory (agents/webchat/directory.py) resolves logins against the live `tenants` container via DefaultAzureCredential (the attached MI holds Cosmos data-plane RBAC — see cosmos-rbac.bicep). Empty string (default) leaves the channel on its hardcoded demo numbers. Only emitted when webchatEnabled is true.')
param cosmosEndpoint string = ''

var containerAppName = 'ca-hello-condomanager-${env}'
var hasIdentity = !empty(userAssignedIdentityId)
// Both must be present — Container Apps secretRef resolution requires the
// identity to read the KV secret at revision start.
var hasAppInsights = hasIdentity && !empty(appInsightsKvSecretUri)
// LangSmith is enabled only when ALL three are set: identity (for KV resolution),
// secret URI, and project name. Missing the project name is a misconfiguration
// (we'd ship a key with no project routing); fail closed rather than send to
// LangSmith's "default" project.
var hasLangsmith = hasIdentity && !empty(langsmithKvSecretUri) && !empty(langsmithProjectName)
// A private ACR pull needs the MI: Container Apps resolves the registry
// `identity` against AcrPull at revision start. Without an identity we can only
// pull public images (the hello-world default), so omit the registries block.
var hasRegistry = hasIdentity && !empty(registryServer)

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: containerAppName
  location: location
  tags: tags
  // The platform rejects an empty userAssignedIdentities map, so we either
  // emit a full UserAssigned identity block or omit `identity` entirely.
  identity: hasIdentity ? {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${userAssignedIdentityId}': {}
    }
  } : null
  properties: {
    environmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: targetPort
        transport: 'auto'
        allowInsecure: false
      }
      // CM-59: authenticate private ACR pulls via the attached MI (which holds
      // AcrPull — see acr-rbac.bicep). Empty for the public hello-world image.
      registries: hasRegistry ? [
        {
          server: registryServer
          identity: userAssignedIdentityId
        }
      ] : []
      // KV-backed secrets resolved through the MI at revision start.
      // Container Apps caches secrets per revision; rotating a value in KV
      // requires a new revision to pick it up — acceptable because the
      // post-deploy seed scripts run once and rotations are operator events.
      //
      // Composed via `union()` so each observability backend (CM-22 AppI,
      // CM-23 LangSmith, future backends) adds its own optional sub-array
      // without nested ternaries.
      secrets: union(
        hasAppInsights ? [
          {
            name: 'appinsights-conn'
            identity: userAssignedIdentityId
            keyVaultUrl: appInsightsKvSecretUri
          }
        ] : [],
        hasLangsmith ? [
          {
            name: 'langsmith-api-key'
            identity: userAssignedIdentityId
            keyVaultUrl: langsmithKvSecretUri
          }
        ] : []
      )
    }
    template: {
      containers: [
        {
          name: 'hello'
          image: image
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          env: union(
            hasAppInsights ? [
              {
                name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
                secretRef: 'appinsights-conn'
              }
            ] : [],
            // CM-23 LangSmith block — all four env vars present together or
            // none. LANGCHAIN_TRACING_V2=true is the SDK toggle; the API key
            // comes via secretRef; project name + endpoint are plaintext.
            hasLangsmith ? [
              {
                name: 'LANGCHAIN_API_KEY'
                secretRef: 'langsmith-api-key'
              }
              {
                name: 'LANGCHAIN_TRACING_V2'
                value: 'true'
              }
              {
                name: 'LANGCHAIN_PROJECT'
                value: langsmithProjectName
              }
              {
                name: 'LANGCHAIN_ENDPOINT'
                value: langsmithEndpoint
              }
            ] : [],
            // CM-59: enable the CM-55 web-chat channel in prod. The flag module
            // (agents/webchat/flag.py) reads this at call time; without it the
            // /web/* endpoints 404, so the channel is inert unless opted in.
            // CM-60: when a cross-origin portal origin is supplied, add
            // WEBCHAT_CORS_ORIGINS so the browser SPA on the SWA can call /web/*.
            // CM-55 follow-up: COSMOS_ENDPOINT lets the web-chat tenant
            // directory resolve logins against the live `tenants` container
            // (DefaultAzureCredential via the attached MI). Unset -> the channel
            // stays on its hardcoded demo numbers.
            webchatEnabled ? concat([
              {
                name: 'WEBCHAT_TEST_ENABLED'
                value: '1'
              }
            ], empty(webchatCorsOrigins) ? [] : [
              {
                name: 'WEBCHAT_CORS_ORIGINS'
                value: webchatCorsOrigins
              }
            ], empty(cosmosEndpoint) ? [] : [
              {
                name: 'COSMOS_ENDPOINT'
                value: cosmosEndpoint
              }
            ]) : []
          )
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
      }
    }
  }
}

output containerAppId string = containerApp.id
output containerAppName string = containerApp.name
output fqdn string = containerApp.properties.configuration.ingress.fqdn
output latestRevisionName string = containerApp.properties.latestRevisionName
