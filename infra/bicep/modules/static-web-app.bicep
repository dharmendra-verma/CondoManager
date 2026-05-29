// static-web-app.bicep — Azure Static Web Apps (Free) for the tenant status portal.
// Jira: CM-37  | Epic: CM-Epic 12 (Tenant/Manager portal)  | Phase 2
//
// Hosts the read-only tenant status portal (static SPA + managed Functions API
// under /api). Provisioned here WITHOUT GitHub repository linkage — code is
// deployed out-of-band via the SWA deployment token (Azure/static-web-apps-deploy
// in deploy.yml), mirroring CM-34's "provision in Bicep, deploy via CI" posture.
//
// Free SKU: one free Static Web App per subscription (100 GB bandwidth/mo).
// The API reads Cosmos via a COSMOS_CONNECTION_STRING app setting that the
// operator seeds from KV post-deploy (SWA Free managed Functions don't support
// Managed Identity) — see docs/INFRA.md. No secrets are set through Bicep.

targetScope = 'resourceGroup'

@description('Deployment environment. Drives resource naming (swa-condomanager-<env>).')
@allowed([ 'dev', 'prod' ])
param env string

@description('Azure region. SWA Free is region-restricted; eastus2 is supported.')
param location string

@description('Tag map produced by tags.bicep.')
param tags object

var staticWebAppName = 'swa-condomanager-${env}'

resource staticWebApp 'Microsoft.Web/staticSites@2023-12-01' = {
  name: staticWebAppName
  location: location
  tags: tags
  sku: {
    name: 'Free'
    tier: 'Free'
  }
  properties: {
    // No repositoryUrl/branch: this is a "bring your own deploy" site driven by
    // the deployment token from CI, not the SWA-managed GitHub integration.
    allowConfigFileUpdates: true
    stagingEnvironmentPolicy: 'Enabled'
  }
}

output staticWebAppName string = staticWebApp.name
output staticWebAppId string = staticWebApp.id
output defaultHostname string = staticWebApp.properties.defaultHostname
