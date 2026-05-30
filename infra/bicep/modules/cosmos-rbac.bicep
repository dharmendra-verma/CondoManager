// cosmos-rbac.bicep — Cosmos DB data-plane RBAC role assignment for the shared MI.
// Jira: CM-38  | Epic: CM-Epic 13 (Security & Compliance)  | Phase 1
//
// This is the Azure-side half of CM-38 AC3 ("field-level access controls in
// Cosmos DB (RBAC)"). Cosmos DB has NO native field/column-level RBAC — its
// data-plane RBAC grants access at the account / database / container
// granularity only. So:
//   * Account-level data-plane access is granted HERE, via a built-in Cosmos
//     SQL role assignment to the CM-18 User-Assigned Managed Identity.
//   * FIELD-level restriction is enforced in application code
//     (agents/security/field_access.py::redact_document).
// The split is documented in docs/SECURITY.md so it isn't mistaken for native
// field RBAC.
//
// We grant the built-in **Data Contributor** role (full data-plane CRUD) scoped
// to the condomanager database. This replaces any reliance on account keys —
// the MI authenticates via AAD (DefaultAzureCredential) and key-based auth can
// then be disabled in a later hardening story. Note: Cosmos SQL role
// assignments are a CONTROL-plane resource (they configure the account), so the
// RG-scoped CI principal (CM-15) can create them — no subscription rights needed.

targetScope = 'resourceGroup'

@description('Deployment environment. Drives the Cosmos account name (cosmos-condomanager-<env>).')
@allowed([ 'dev', 'prod' ])
param env string

@description('Object ID (principalId) of the User-Assigned MI that receives data-plane access. From managed-identity.bicep outputs.principalId.')
param principalId string

@description('Cosmos SQL database the role assignment is scoped to. Default matches cosmos.bicep.')
param databaseName string = 'condomanager'

var accountName = 'cosmos-condomanager-${env}'

// Built-in Cosmos DB SQL role: "Cosmos DB Built-in Data Contributor"
// (full data-plane CRUD). Reader is 0000…0001; Contributor is 0000…0002.
// https://learn.microsoft.com/azure/cosmos-db/how-to-setup-rbac#built-in-role-definitions
var dataContributorRoleId = '00000000-0000-0000-0000-000000000002'

// Reference the account provisioned by cosmos.bicep (same RG, same deployment).
resource account 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: accountName
}

// guid(scope, principal, role) makes the assignment name deterministic so
// redeploys are idempotent (mirrors the keyvault.bicep pattern).
resource dataPlaneAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: account
  name: guid(account.id, principalId, dataContributorRoleId)
  properties: {
    roleDefinitionId: '${account.id}/sqlRoleDefinitions/${dataContributorRoleId}'
    principalId: principalId
    // Scope to the database so the grant covers every container (tickets,
    // audit, conversations, …) without enumerating each one.
    scope: '${account.id}/dbs/${databaseName}'
  }
}

output roleAssignmentId string = dataPlaneAssignment.id
output roleAssignmentName string = dataPlaneAssignment.name
