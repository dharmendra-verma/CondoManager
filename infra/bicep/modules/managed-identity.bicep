// managed-identity.bicep — User-Assigned Managed Identity shared by all CondoManager workloads.
// Jira: CM-18  | Epic: CM-1 (Foundation & Azure Infrastructure)  | Phase 0
//
// One MI per environment (`id-condomanager-<env>`), attached to every workload
// that needs to read secrets from Key Vault (Container Apps today; Functions /
// Jobs / etc. trivially reuse the same identity if introduced later).
//
// User-Assigned over System-Assigned:
//   * Survives Container App re-creation (system-assigned MI is destroyed with
//     the app and gets a new principalId, which breaks all RBAC bindings).
//   * Can be granted RBAC *before* any app exists — no chicken-and-egg in CI.
//   * Shared by N workloads with ONE role assignment per resource, not N.

targetScope = 'resourceGroup'

@description('Deployment environment. Drives resource naming (id-condomanager-<env>).')
@allowed([ 'dev', 'prod' ])
param env string

@description('Azure region. Passthrough from main.bicep.')
param location string

@description('Tag map produced by tags.bicep.')
param tags object

var identityName = 'id-condomanager-${env}'

resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
  tags: tags
}

output identityId string = managedIdentity.id
output identityName string = managedIdentity.name
// principalId is the AAD object ID — what RBAC role assignments bind to.
output principalId string = managedIdentity.properties.principalId
// clientId is the AAD application ID — what DefaultAzureCredential's
// AZURE_CLIENT_ID env var uses to pick this MI when multiple are attached.
output clientId string = managedIdentity.properties.clientId
