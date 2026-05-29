// action-group.bicep — Azure Monitor Action Group fired by all CM-26 alerts.
// Jira: CM-26  | Epic: Observability  | Phase 0
//
// One Action Group per env (`ag-condomanager-<env>`), shared by the
// CM-26 budget thresholds (50/80/100%) and the three scheduled-query
// alert rules (latency SLO, guardrail trips, hallucination spikes).
// Single Action Group = single place an operator changes pager
// destinations.
//
// Receivers (email + Slack webhook) are conditional on non-empty params
// so an empty deploy is legal. With both empty, the Action Group fires
// when alerts trigger but pages nobody — same posture as CM-22's
// connection-string secret. Operator fills via `--parameters
// alertSlackWebhookUrl=... alertEmail=...` at deploy time OR via the
// Azure Portal post-deploy (Action Group → Notifications → add).
//
// `useCommonAlertSchema: true` normalizes the JSON shape Azure sends to
// every receiver — operators write one Slack message formatter and
// reuse it across all alert types.

targetScope = 'resourceGroup'

@description('Deployment environment. Drives the resource name (ag-condomanager-<env>).')
@allowed([ 'dev', 'prod' ])
param env string

@description('Tag map produced by tags.bicep.')
param tags object

@description('Email recipient address. Empty string omits the email receiver entirely (legal empty-deploy posture).')
param emailAddress string = ''

@description('Slack incoming webhook URL. Empty string omits the webhook receiver entirely. @secure() so the URL stays out of deployment-history plaintext — anyone with the URL can post to your Slack.')
@secure()
param slackWebhookUrl string = ''

var actionGroupName = 'ag-condomanager-${env}'
// groupShortName: ≤12 chars; shown in SMS / push notifications. Truncate to
// env-suffixed shorthand so the alert text fits the SMS character limit.
var shortName = 'cm-${env}'

var hasEmail = !empty(emailAddress)
var hasSlack = !empty(slackWebhookUrl)

resource actionGroup 'Microsoft.Insights/actionGroups@2023-09-01-preview' = {
  name: actionGroupName
  // Action Groups MUST be in `global` regardless of caller's intent; the
  // resource type doesn't accept regional locations. Bicep enforces this.
  location: 'global'
  tags: tags
  properties: {
    groupShortName: shortName
    enabled: true
    emailReceivers: hasEmail ? [
      {
        name: 'ops-email'
        emailAddress: emailAddress
        // Consistent payload shape — operators write one formatter once.
        useCommonAlertSchema: true
      }
    ] : []
    webhookReceivers: hasSlack ? [
      {
        name: 'slack-webhook'
        serviceUri: slackWebhookUrl
        useCommonAlertSchema: true
        // Slack's incoming webhooks don't need AAD auth; the URL is the
        // auth token. useAadAuth: true would force a managed-identity
        // exchange Slack doesn't speak.
        useAadAuth: false
      }
    ] : []
  }
}

output actionGroupId string = actionGroup.id
output actionGroupName string = actionGroup.name
