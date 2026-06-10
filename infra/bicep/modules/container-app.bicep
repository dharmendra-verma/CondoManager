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
// CM-65: Langfuse production keys (`langfuse-public-key`, `langfuse-secret-key`)
// follow the same KV→secretRef pattern as APPLICATIONINSIGHTS_CONNECTION_STRING.
// When `langfusePublicKeyKvSecretUri` + `langfuseSecretKeyKvSecretUri` are
// supplied (alongside the MI), the platform pulls both keys from KV at revision
// start and exposes them as LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY, plus the
// plaintext LANGFUSE_HOST. `agents/observability/langfuse_export.py` reads them
// from `os.environ`; both keys unset (the default) means Langfuse stays disabled
// — see is_langfuse_enabled() in that module. main.bicep enables this in prod
// (the tracing-backend split: LangSmith=dev, Langfuse=prod — never both, to
// avoid double-paying and emitting twice per call).

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

@description('Key Vault secret URI for the Langfuse PUBLIC key (CM-65). Empty string (default) omits the Langfuse env vars. Requires userAssignedIdentityId and a non-empty langfuseSecretKeyKvSecretUri — both keys are needed for is_langfuse_enabled().')
param langfusePublicKeyKvSecretUri string = ''

@description('Key Vault secret URI for the Langfuse SECRET key (CM-65). Empty string (default) omits the Langfuse env vars. Requires userAssignedIdentityId and a non-empty langfusePublicKeyKvSecretUri.')
param langfuseSecretKeyKvSecretUri string = ''

@description('Langfuse host routed via LANGFUSE_HOST (CM-65). Plaintext (not a secret); the Cloud default. Override for self-hosted/EU Langfuse.')
param langfuseHost string = 'https://cloud.langfuse.com'

@description('Azure OpenAI endpoint for the Knowledge agent embeddings (CM-75). Emitted as AZURE_OPENAI_ENDPOINT. Empty string (default) omits the Azure OpenAI env vars, leaving the Knowledge agent embedder unconfigured so RAG refuses every policy question. Pairs with azureOpenAiKeyKvSecretUri — both required.')
param azureOpenAiEndpoint string = ''

@description('Key Vault secret URI for the Azure OpenAI API key (CM-75). Emitted as AZURE_OPENAI_API_KEY via secretRef. Empty string (default) omits the Azure OpenAI env vars. Requires userAssignedIdentityId (to resolve the secretRef) and a non-empty azureOpenAiEndpoint.')
param azureOpenAiKeyKvSecretUri string = ''

@description('Azure OpenAI chat deployment name for the agent LLM seams (CM-79). Emitted as AZURE_OPENAI_CHAT_DEPLOYMENT (only when the Azure OpenAI env vars above are set). Defaults to the gpt-4o-mini deployment on the shared Azure OpenAI resource; the agents read it via agents/chat.py.')
param azureOpenAiChatDeployment string = 'gpt-4o-mini'

@description('Azure OpenAI data-plane API version for chat (CM-79). Emitted as AZURE_OPENAI_API_VERSION (only when the Azure OpenAI env vars are set). A GA version that supports tool/function calling — required for the agent structured outputs.')
param azureOpenAiApiVersion string = '2024-10-21'

@description('ACR login server (e.g. acrcondomanager<env>.azurecr.io) for a PRIVATE image pull (CM-59). Empty string (default) omits the registries block — back-compat for the public hello-world image, which needs no auth. When set, requires userAssignedIdentityId: the MI authenticates the pull and must hold AcrPull (see acr-rbac.bicep).')
param registryServer string = ''

@description('When true, sets WEBCHAT_TEST_ENABLED=1 so the CM-55 web-chat channel is live — the prod inbound entry point (CM-59). Default false keeps the endpoints 404 ("not deployed") for the hello-world shell and any caller that does not opt in.')
param webchatEnabled bool = false

@description('Comma-separated extra CORS origins for the web-chat app (CM-60), set as WEBCHAT_CORS_ORIGINS so the prod portal (Static Web App) — a different origin than this Container App — can call /web/* from the browser. Empty (default) leaves only the localhost dev origins the app hardcodes. Only emitted when webchatEnabled is true.')
param webchatCorsOrigins string = ''

@description('Cosmos DB account endpoint (CM-17). Emitted as COSMOS_ENDPOINT so the web-chat tenant directory (agents/webchat/directory.py) resolves logins against the live `tenants` container via DefaultAzureCredential (the attached MI holds Cosmos data-plane RBAC — see cosmos-rbac.bicep). Empty string (default) leaves the channel on its hardcoded demo numbers. Only emitted when webchatEnabled is true.')
param cosmosEndpoint string = ''

@description('Client (application) ID of the attached User-Assigned MI, emitted as AZURE_CLIENT_ID. REQUIRED for in-app DefaultAzureCredential to acquire a token for a USER-assigned identity — without it ManagedIdentityCredential fails with "Unable to load the proper Managed Identity" (the container has no system-assigned MI to fall back to). See managed-identity.bicep clientId output. Empty string (default) omits the env var (back-compat for the public hello-world shell with no identity).')
param managedIdentityClientId string = ''

var containerAppName = 'ca-hello-condomanager-${env}'
var hasIdentity = !empty(userAssignedIdentityId)
// AZURE_CLIENT_ID lets DefaultAzureCredential target the user-assigned MI. Only
// useful (and only correct) when an identity is actually attached.
var hasMiClientId = hasIdentity && !empty(managedIdentityClientId)
// Both must be present — Container Apps secretRef resolution requires the
// identity to read the KV secret at revision start.
var hasAppInsights = hasIdentity && !empty(appInsightsKvSecretUri)
// LangSmith is enabled only when ALL three are set: identity (for KV resolution),
// secret URI, and project name. Missing the project name is a misconfiguration
// (we'd ship a key with no project routing); fail closed rather than send to
// LangSmith's "default" project.
var hasLangsmith = hasIdentity && !empty(langsmithKvSecretUri) && !empty(langsmithProjectName)
// CM-65: Langfuse needs BOTH keys (public + secret) plus the identity to resolve
// the secretRefs. Requiring both URIs fails closed — a half-config never
// half-mounts (is_langfuse_enabled() needs both keys present anyway).
var hasLangfuse = hasIdentity && !empty(langfusePublicKeyKvSecretUri) && !empty(langfuseSecretKeyKvSecretUri)
// CM-75: the Knowledge agent's embedder needs BOTH the endpoint (plaintext) and
// the API key (secretRef, resolved by the MI). Require both + identity so a
// half-config never mounts a key with no endpoint (or an endpoint with no key).
var hasAzureOpenAi = hasIdentity && !empty(azureOpenAiEndpoint) && !empty(azureOpenAiKeyKvSecretUri)
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
        ] : [],
        // CM-65: Langfuse public + secret keys, both resolved via the MI.
        hasLangfuse ? [
          {
            name: 'langfuse-public-key'
            identity: userAssignedIdentityId
            keyVaultUrl: langfusePublicKeyKvSecretUri
          }
          {
            name: 'langfuse-secret-key'
            identity: userAssignedIdentityId
            keyVaultUrl: langfuseSecretKeyKvSecretUri
          }
        ] : [],
        // CM-75: Azure OpenAI API key for the Knowledge agent's embedder,
        // resolved from KV via the MI (same pattern as the Langfuse keys).
        hasAzureOpenAi ? [
          {
            name: 'azure-openai-key'
            identity: userAssignedIdentityId
            keyVaultUrl: azureOpenAiKeyKvSecretUri
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
            // AZURE_CLIENT_ID picks the user-assigned MI for any in-app
            // DefaultAzureCredential (e.g. the web-chat Cosmos lookup). Required
            // for user-assigned identities — see managed-identity.bicep.
            hasMiClientId ? [
              {
                name: 'AZURE_CLIENT_ID'
                value: managedIdentityClientId
              }
            ] : [],
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
            // CM-65 Langfuse block — both keys via secretRef; host is plaintext.
            // CM-63 already calls init_langfuse() at startup; these env vars are
            // what flip is_langfuse_enabled() true on the running container.
            hasLangfuse ? [
              {
                name: 'LANGFUSE_PUBLIC_KEY'
                secretRef: 'langfuse-public-key'
              }
              {
                name: 'LANGFUSE_SECRET_KEY'
                secretRef: 'langfuse-secret-key'
              }
              {
                name: 'LANGFUSE_HOST'
                value: langfuseHost
              }
            ] : [],
            // CM-75: Azure OpenAI config for the Knowledge agent's embedder (RAG
            // over policies-vector). Without these, default_embedder() returns None
            // and every policy question refuses -> Maintenance. Endpoint is
            // plaintext; the API key comes via secretRef (MI-resolved from KV).
            // CM-79: the same endpoint + key now also back the chat LLM seams
            // (triage / knowledge / coordinator / synthesis / escalation) via
            // agents/chat.py — AZURE_OPENAI_CHAT_DEPLOYMENT + AZURE_OPENAI_API_VERSION
            // name the chat deployment so prod runs the real GPT-4o-mini instead of
            // the offline stubs/heuristics. No new secret (reuses azure-openai-key).
            hasAzureOpenAi ? [
              {
                name: 'AZURE_OPENAI_ENDPOINT'
                value: azureOpenAiEndpoint
              }
              {
                name: 'AZURE_OPENAI_API_KEY'
                secretRef: 'azure-openai-key'
              }
              {
                name: 'AZURE_OPENAI_CHAT_DEPLOYMENT'
                value: azureOpenAiChatDeployment
              }
              {
                name: 'AZURE_OPENAI_API_VERSION'
                value: azureOpenAiApiVersion
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
