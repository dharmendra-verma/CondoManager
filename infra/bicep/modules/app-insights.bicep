// app-insights.bicep — workspace-based Application Insights component.
// Jira: CM-22  | Epic: Observability  | Phase 0
//
// One App Insights resource per environment (`appi-condomanager-<env>`),
// in workspace-based mode — i.e. all telemetry storage backs onto the
// existing Log Analytics workspace from CM-16 (`law-condomanager-<env>`).
// Classic non-workspace App Insights was retired Feb 2024; this is the
// only supported form going forward.
//
// The Python side (CM-21's `agents/observability/sdk.py`, extended in
// CM-22) reads `APPLICATIONINSIGHTS_CONNECTION_STRING` and delegates
// to the `azure-monitor-opentelemetry` distro when set. The Container
// App pulls that connection string from Key Vault (CM-18) via native
// `secretRef`, resolved through the existing User-Assigned MI.
//
// Sampling has two layers:
//   1. SDK-side (Parent-based with TraceIdRatioBased) — Python sets the
//      ratio from `OTEL_SAMPLER_RATIO` env var (default 1.0).
//   2. Server-side at this resource — `SamplingPercentage` (0-100 int)
//      drives App Insights' adaptive sampling. Dev defaults to 100
//      (no server-side drop); prod defaults to 50 (halve at ingestion).
// Live Metrics ride above the sampler and stay unsampled, so the
// cost/latency dashboards stay sharp even in heavily sampled prod.

targetScope = 'resourceGroup'

@description('Deployment environment. Drives resource naming (appi-condomanager-<env>).')
@allowed([ 'dev', 'prod' ])
param env string

@description('Azure region. Passthrough from main.bicep.')
param location string

@description('Tag map produced by tags.bicep.')
param tags object

@description('Resource ID of the Log Analytics workspace from CM-16. App Insights writes spans + metrics into this workspace (workspace-based mode).')
param logAnalyticsWorkspaceId string

@description('Server-side adaptive sampling percentage on the App Insights resource. 100 = no drop; lower values reduce ingestion cost. Dev defaults to 100; prod defaults to 50 (set in main.bicep).')
@minValue(0)
@maxValue(100)
param samplingPercentage int = 100

var appInsightsName = 'appi-condomanager-${env}'

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    // Flow_Type: 'Bluefield' + IngestionMode: 'LogAnalytics' is the
    // workspace-based configuration. WorkspaceResourceId MUST be set
    // alongside these or the resource falls back to the deprecated classic mode.
    Flow_Type: 'Bluefield'
    Request_Source: 'rest'
    WorkspaceResourceId: logAnalyticsWorkspaceId
    IngestionMode: 'LogAnalytics'
    SamplingPercentage: samplingPercentage
    DisableIpMasking: false
    // Public ingestion is fine for now; private endpoint is a future story
    // (matches the posture from CM-18's Key Vault).
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

output appInsightsName string = appInsights.name
output appInsightsId string = appInsights.id
// @secure() keeps the connection string out of deployment-history plaintext.
// `seed-app-insights-secret.sh` reads it via `az deployment group show
// --query 'properties.outputs.appInsightsConnectionString.value'` and writes
// it into the Key Vault secret `app-insights-connection-string`.
@secure()
output connectionString string = appInsights.properties.ConnectionString
