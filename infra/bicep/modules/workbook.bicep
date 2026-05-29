// workbook.bicep — Operations workbook over App Insights / LAW.
// Jira: CM-25  | Epic: Observability  | Phase 0
//
// Provisions a `Microsoft.Insights/workbooks` resource with sourceId
// pointed at the CM-22 App Insights component. The serialized workbook
// payload (5 sections: header + time-range parameter + 4 KQL panels for
// cost / latency / errors / HITL queue depth) lives next to this Bicep
// file as `workbook-payload.json` so it stays diffable and hand-editable.
//
// Most panels will be empty until CM-28 (LangGraph spine) and CM-30
// (Triage Agent) ship live traffic — the workbook is built now to lock
// the OTel attribute schema (gen_ai.* / openinference.*) and the
// hitl.queued / hitl.resolved customEvents contract that future stories
// emit against. See docs/OBSERVABILITY.md for the canonical KQL queries
// and the operator pin-to-dashboard step.

targetScope = 'resourceGroup'

@description('Deployment environment. Drives the displayName suffix; the underlying resource name is a deterministic guid().')
@allowed([ 'dev', 'prod' ])
param env string

@description('Azure region. Passthrough from main.bicep.')
param location string

@description('Tag map produced by tags.bicep.')
param tags object

@description('Resource ID of the App Insights component (CM-22) the workbook queries. MUST match the appInsights module output — wrong sourceId means queries fail at runtime.')
param appInsightsId string

@description('Display name shown in the Azure Workbooks portal blade. Default makes the env obvious in the workbook list.')
param displayName string = 'CondoManager Ops — ${env}'

// Workbook `name` MUST parse as a GUID. Use guid() with stable inputs so
// redeployments are idempotent (same RG + same env -> same name).
var workbookName = guid(resourceGroup().id, 'condomanager-ops', env)

resource workbook 'Microsoft.Insights/workbooks@2023-06-01' = {
  name: workbookName
  location: location
  tags: tags
  // `shared` makes the workbook visible to everyone in the RG via the
  // App Insights "Workbooks → My workbooks" tab. The alternative `user`
  // would scope it to whichever principal happened to deploy.
  kind: 'shared'
  properties: {
    displayName: displayName
    // `workbook` is the standard category for ops workbooks (vs `sentinel`,
    // `tsg`, etc. which gate visibility to other Azure blades).
    category: 'workbook'
    // sourceId MUST be the App Insights component ID — the workbook UI
    // uses this to scope the default query context. Passing the LAW
    // would break the per-component scoping the panels expect.
    sourceId: appInsightsId
    serializedData: loadTextContent('./workbook-payload.json')
    version: 'Notebook/1.0'
  }
}

output workbookId string = workbook.id
output workbookName string = workbook.name
output workbookDisplayName string = displayName
