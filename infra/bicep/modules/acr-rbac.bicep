// acr-rbac.bicep — grant the shared User-Assigned MI AcrPull on the registry.
// Jira: CM-59  | Epic: CM-1 (Foundation & Azure Infrastructure)  | Phase 1
//
// CM-20 created the registry but explicitly DEFERRED the pull grant ("Pull
// (future): future Container Apps' User-Assigned MI ... granted AcrPull. Out of
// scope for CM-20."). CM-59 is that future consumer: the agent-runtime Container
// App pulls acrcondomanager<env>.azurecr.io/agent:<tag> using the MI, so the MI
// needs AcrPull on the registry. Mirrors cosmos-rbac.bicep — a control-plane
// role assignment the RG-scoped CI principal (CM-15) can create.
//
// The assignment is harmless while the app still runs the public hello-world
// image (an unused grant), so main.bicep wires it unconditionally and serializes
// the Container App behind it (dependsOn) so the role exists before the first
// ACR pull at revision start.

targetScope = 'resourceGroup'

@description('Deployment environment. Drives the registry name (acrcondomanager<env>).')
@allowed([ 'dev', 'prod' ])
param env string

@description('Object ID (principalId) of the User-Assigned MI that receives AcrPull. From managed-identity.bicep outputs.principalId.')
param principalId string

// ACR naming exception (no hyphens) — matches acr.bicep.
var acrName = 'acrcondomanager${env}'

// Built-in role: AcrPull — pull manifests + blobs, no push/delete.
// https://learn.microsoft.com/azure/role-based-access-control/built-in-roles#acrpull
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

// Reference the registry provisioned by acr.bicep (same RG, same deployment).
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
}

// guid(scope, principal, role) makes the assignment name deterministic so
// redeploys are idempotent (mirrors keyvault.bicep / cosmos-rbac.bicep).
resource acrPullAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, principalId, acrPullRoleId)
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: principalId
    // ServicePrincipal is correct for MI object IDs (User/Group would trigger a
    // user-directory lookup that fails for a managed identity).
    principalType: 'ServicePrincipal'
  }
}

output roleAssignmentId string = acrPullAssignment.id
output roleAssignmentName string = acrPullAssignment.name
