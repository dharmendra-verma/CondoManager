// container-app.bicep — Hello-world Container App (smoke-test surface).
// Jira: CM-16 (initial)  | CM-18 (User-Assigned MI)  | CM-22 (App Insights via KV secretRef)
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

var containerAppName = 'ca-hello-condomanager-${env}'
var hasIdentity = !empty(userAssignedIdentityId)
// Both must be present — Container Apps secretRef resolution requires the
// identity to read the KV secret at revision start.
var hasAppInsights = hasIdentity && !empty(appInsightsKvSecretUri)

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
      // KV-backed secret resolved through the MI at revision start.
      // Container Apps caches the secret per revision; rotating the value in
      // KV requires a new revision to pick it up — acceptable because the
      // post-deploy seed script runs once and rotations are operator events.
      secrets: hasAppInsights ? [
        {
          name: 'appinsights-conn'
          identity: userAssignedIdentityId
          keyVaultUrl: appInsightsKvSecretUri
        }
      ] : []
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
          env: hasAppInsights ? [
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              secretRef: 'appinsights-conn'
            }
          ] : []
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
