// functions.bicep — Azure Functions app (Linux Consumption) for the
// Google Drive → Cosmos vector sync job.
// Jira: CM-34  | Epic: CM-Epic 9 (Knowledge ingestion)  | Phase 1
//
// Provisions a Y1 (Consumption) Linux Python 3.12 Function App + its own
// storage account. The function code (functions/gdrive-sync/) is deployed
// out-of-band via `func azure functionapp publish` — see docs/INFRA.md. This
// module only provisions the resource + wires its configuration.
//
// Auth: the shared CM-18 User-Assigned MI is attached so the app reads Cosmos
// (DefaultAzureCredential) and resolves Key Vault references. Secrets
// (google-drive-sa-key, azure-openai-key) are mounted as Key Vault references
// rather than literal values, so no secret material lands in app settings.
//
// Cost: Y1 Consumption is pay-per-execution with scale-to-zero — a 30-minute
// timer is a handful of invocations/hour, comfortably inside the free grant
// (1M executions + 400,000 GB-s per month).

targetScope = 'resourceGroup'

@description('Deployment environment. Drives resource naming (func-condomanager-<env>).')
@allowed([ 'dev', 'prod' ])
param env string

@description('Azure region. Passthrough from main.bicep.')
param location string

@description('Tag map produced by tags.bicep.')
param tags object

@description('Resource ID of the shared User-Assigned MI (CM-18). Attached to the Function App for Cosmos data-plane access + Key Vault reference resolution.')
param userAssignedIdentityId string

@description('Application Insights connection string (CM-22). @secure() keeps the embedded InstrumentationKey out of deployment-history plaintext.')
@secure()
param appInsightsConnectionString string

@description('Cosmos DB account endpoint (CM-17). The sync job reads/writes policies-vector + knowledge_sync via DefaultAzureCredential.')
param cosmosEndpoint string

@description('Key Vault URI (CM-18), ending in "/". Used to build Key Vault references for the Google SA key + Azure OpenAI key.')
param keyVaultUri string

@description('Google Drive folder id the sync job watches. Empty until an operator supplies it; the function skips cleanly when unset.')
param gdriveFolderId string = ''

@description('Tenant id the synced docs are attributed to (Cosmos /tenantId partition).')
param gdriveTenantId string = 'default'

@description('Azure OpenAI endpoint for embeddings. Empty until provisioned; the function skips cleanly when unset.')
param azureOpenAiEndpoint string = ''

@description('Azure OpenAI embedding deployment name. Defaults to the text-embedding-3-small model that matches the CM-17 1536-dim vector index.')
param azureOpenAiEmbeddingDeployment string = 'text-embedding-3-small'

// Storage account names: 3–24 chars, lowercase alphanumeric only (no hyphens),
// globally unique — same constraint family as ACR (CM-20). Deterministic name
// so docs/tooling can find it.
var storageAccountName = 'stcondomanager${env}fn'
var functionAppName = 'func-condomanager-${env}'
var planName = 'plan-condomanager-${env}-fn'

var googleSaKeyRef = '@Microsoft.KeyVault(SecretUri=${keyVaultUri}secrets/google-drive-sa-key)'
var azureOpenAiKeyRef = '@Microsoft.KeyVault(SecretUri=${keyVaultUri}secrets/azure-openai-key)'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  tags: tags
  kind: 'functionapp'
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  properties: {
    // Linux plan.
    reserved: true
  }
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionAppName
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${userAssignedIdentityId}': {}
    }
  }
  properties: {
    serverFarmId: plan.id
    reserved: true
    httpsOnly: true
    // Resolve @Microsoft.KeyVault(...) references using the User-Assigned MI
    // (the same MI granted Key Vault Secrets User in keyvault.bicep).
    keyVaultReferenceIdentity: userAssignedIdentityId
    siteConfig: {
      linuxFxVersion: 'Python|3.12'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: [
        {
          name: 'AzureWebJobsStorage'
          value: 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'
        }
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'WEBSITE_RUN_FROM_PACKAGE'
          value: '1'
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsightsConnectionString
        }
        {
          name: 'ENVIRONMENT'
          value: env
        }
        {
          name: 'COSMOS_ENDPOINT'
          value: cosmosEndpoint
        }
        {
          name: 'GDRIVE_FOLDER_ID'
          value: gdriveFolderId
        }
        {
          name: 'GDRIVE_TENANT_ID'
          value: gdriveTenantId
        }
        {
          name: 'AZURE_OPENAI_ENDPOINT'
          value: azureOpenAiEndpoint
        }
        {
          name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT'
          value: azureOpenAiEmbeddingDeployment
        }
        {
          name: 'AZURE_OPENAI_API_VERSION'
          value: '2024-02-01'
        }
        {
          name: 'GOOGLE_DRIVE_SA_KEY'
          value: googleSaKeyRef
        }
        {
          name: 'AZURE_OPENAI_API_KEY'
          value: azureOpenAiKeyRef
        }
      ]
    }
  }
}

output functionAppName string = functionApp.name
output functionAppId string = functionApp.id
output functionAppDefaultHostName string = functionApp.properties.defaultHostName
output storageAccountName string = storage.name
