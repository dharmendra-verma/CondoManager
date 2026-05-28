// acr.bicep — Azure Container Registry (Basic SKU) for CondoManager base images.
// Jira: CM-20  | Epic: CM-1 (Foundation & Azure Infrastructure)  | Phase 0
//
// One registry per environment, naming exception: ACR forbids hyphens in
// registry names (alphanumeric only, 5-50 chars, globally unique), so the
// established `<resource>-condomanager-<env>` pattern cannot apply. We use
// `acrcondomanager<env>` (same tokens, no hyphens) and document this in
// docs/INFRA.md as the one exception.
//
// SKU: Basic (per CM-20 AC #1). Premium would give geo-replication, private
// endpoint, content trust, and the built-in retention policy
// (Microsoft.ContainerRegistry/registries/policies/retentionPolicy) — defer
// all of those until a real consumer needs them.
//
// Retention: ACR's built-in `policies.retentionPolicy` is Premium-only.
// On Basic we substitute `infra/scripts/acr-prune.sh` (run on the weekly cron
// of `.github/workflows/base-image.yml`) which keeps the last N tagged
// manifests per repo and deletes untagged manifests.
//
// Auth posture:
//   * Admin user disabled — never. Username/password defeats the "no static
//     credentials" stance from CM-18.
//   * Push: GitHub Actions OIDC SP, granted `AcrPush` out-of-band after the
//     first deploy (documented in docs/INFRA.md). RG-Contributor from CM-15
//     is control-plane only and does NOT include data-plane push.
//   * Pull (future): future Container Apps' User-Assigned MI from CM-18,
//     granted `AcrPull`. Out of scope for CM-20.

targetScope = 'resourceGroup'

@description('Deployment environment. Drives resource naming (acrcondomanager<env>).')
@allowed([ 'dev', 'prod' ])
param env string

@description('Azure region. Passthrough from main.bicep.')
param location string

@description('Tag map produced by tags.bicep.')
param tags object

@description('ACR SKU. AC #1 fixes Basic; Premium would unlock the built-in retention policy, geo-replication, and private endpoint.')
@allowed([ 'Basic', 'Standard', 'Premium' ])
param sku string = 'Basic'

@description('Admin user (username + password). Always false — push uses OIDC + AcrPush, pull uses MI + AcrPull. Static creds defeat the CM-18 secret-store posture.')
param adminUserEnabled bool = false

// ACR naming exception — hyphens are forbidden, so the suffix is concatenated
// directly. This is the only resource that doesn't follow the
// `<resource>-condomanager-<env>` convention.
var acrName = 'acrcondomanager${env}'

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  tags: tags
  sku: {
    name: sku
  }
  properties: {
    adminUserEnabled: adminUserEnabled
    publicNetworkAccess: 'Enabled'        // tighten to private endpoint when Premium lands
    anonymousPullEnabled: false           // require auth on every pull
    // policies.retentionPolicy is Premium-only — see acr-prune.sh for the
    // Basic-SKU substitute (cron-driven; keeps last 5 tagged manifests).
  }
}

output acrName string = acr.name
output acrId string = acr.id
output loginServer string = acr.properties.loginServer
