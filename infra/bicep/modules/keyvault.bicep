// keyvault.bicep — Azure Key Vault (RBAC mode) + role assignment for the shared MI.
// Jira: CM-18  | Epic: CM-1 (Foundation & Azure Infrastructure)  | Phase 0
//
// One vault per environment (`kv-condomanager-<env>`), RBAC-only (no access
// policies), with soft-delete + purge protection on. The User-Assigned MI
// from managed-identity.bicep is granted `Key Vault Secrets User` so any
// workload bearing that MI can read secret values via DefaultAzureCredential.
//
// Why secret VALUES are NOT set in Bicep:
//   * Bicep secret resources require a non-empty `value` property.
//   * That value would then live in source, deployment history, and any
//     exported ARM template. None of those are acceptable for real secrets.
// So Bicep declares the vault + the role binding; the secret-name *schema*
// is documented here (and in docs/INFRA.md), and seed-keyvault-secrets.sh
// performs the first `az keyvault secret set` out-of-band with the literal
// placeholder `REPLACE-ME`. Operators then replace placeholders with real
// values via another `az keyvault secret set` call. See:
//   infra/scripts/seed-keyvault-secrets.sh
//   docs/INFRA.md  (Key Vault & Secret Rotation section)

targetScope = 'resourceGroup'

@description('Deployment environment. Drives resource naming (kv-condomanager-<env>).')
@allowed([ 'dev', 'prod' ])
param env string

@description('Azure region. Passthrough from main.bicep.')
param location string

@description('Tag map produced by tags.bicep.')
param tags object

@description('Object ID (principalId) of the User-Assigned MI that gets Key Vault Secrets User on this vault.')
param managedIdentityPrincipalId string

@description('Documentation-only list of secret names this vault is expected to hold. Seeded by infra/scripts/seed-keyvault-secrets.sh with the placeholder REPLACE-ME so the schema exists; real values are set out-of-band. The lint test diffs this list against the seed script to prevent drift.')
#disable-next-line no-unused-params // CM-43: param is consumed by seed-keyvault-secrets.sh + test_bicep_lint.sh, not by Bicep itself
param secretNames array = [
  'azure-openai-key'
  'twilio-account-sid'
  'twilio-auth-token'
  'twilio-whatsapp-number'
  'langsmith-api-key'
  'langfuse-public-key'
  'langfuse-secret-key'
  'cosmos-connection-string'
  // CM-22: Container Apps mounts this as APPLICATIONINSIGHTS_CONNECTION_STRING
  // via secretRef. seed-app-insights-secret.sh populates it from the deployment
  // output post-deploy; until then it sits as the CM-18 REPLACE-ME placeholder.
  'app-insights-connection-string'
  // CM-34: Google service-account JSON key for the Drive → Cosmos sync job.
  // The gdrive-sync Function App reads it via a Key Vault reference; operators
  // paste the SA key out-of-band (see docs/INFRA.md). Placeholder until then.
  'google-drive-sa-key'
]

var vaultName = 'kv-condomanager-${env}'
// Built-in role: Key Vault Secrets User — read secret values only.
// https://learn.microsoft.com/azure/role-based-access-control/built-in-roles#key-vault-secrets-user
var kvSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: vaultName
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true          // AC #1 — RBAC mode (no access policies)
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true            // irreversible; vault name reserved 90d after delete
    publicNetworkAccess: 'Enabled'         // tighten to private endpoint in a later story
    // CM-43: minimumTlsVersion property was removed — BCP037 (not valid on
    // VaultProperties). Key Vault enforces TLS 1.2+ by default and exposes no
    // configurable knob (unlike Cosmos / Storage). The property was silently
    // ignored when previously set.
    // networkAcls is a no-op while publicNetworkAccess is 'Enabled' (the
    // firewall only gates traffic when public access is restricted). Set
    // here so the desired posture is already in place when a later story
    // flips publicNetworkAccess to 'Disabled' + private endpoint.
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

// MI → Key Vault Secrets User binding. guid() makes the assignment name
// deterministic from (scope, principal, role) so redeploys are idempotent.
resource kvSecretsUserAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, managedIdentityPrincipalId, kvSecretsUserRoleId)
  scope: vault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalId: managedIdentityPrincipalId
    // ServicePrincipal is the correct value for MI object IDs — User/Group
    // would cause Azure to try to look up the principal in a user directory.
    principalType: 'ServicePrincipal'
  }
}

output vaultName string = vault.name
output vaultId string = vault.id
output vaultUri string = vault.properties.vaultUri
// secretNames intentionally NOT output — it's a documentation-only param
// default that the seed script duplicates and the lint test diffs against.
// Surfacing it here adds noise to deployment outputs without a consumer.
