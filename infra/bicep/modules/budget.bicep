// budget.bicep — Azure Consumption budget with 50/80/100% threshold alerts.
// Jira: CM-26 (AC #1)  | Epic: Observability  | Phase 0
//
// One Consumption Budget per env (`budget-condomanager-<env>`) scoped to
// the RG. Three notifications fire at 50%, 80%, and 100% of *Actual*
// (not Forecasted) spend; each notifies the shared CM-26 Action Group
// AND the RG Owner role as a backstop in case the operator hasn't
// populated the Action Group's email receiver yet.
//
// Why RG-scoped: no Microsoft.CognitiveServices/accounts (Azure OpenAI)
// resource exists in the project yet, so we can't filter spend to
// "OpenAI only". When CM-OpenAI lands the OpenAI resource will dominate
// RG spend anyway — operator can tighten the filter to a ResourceType
// filter then. Documented in docs/OBSERVABILITY.md.

targetScope = 'resourceGroup'

@description('Deployment environment. Drives the resource name (budget-condomanager-<env>).')
@allowed([ 'dev', 'prod' ])
param env string

@description('Resource ID of the Action Group fired when a budget threshold is crossed.')
param actionGroupId string

@description('Monthly budget in USD. Operator tunes per env after first month of real usage data.')
@minValue(1)
param monthlyAmountUsd int

@description('First day of the budget window. Defaults to the first of the current UTC month — Bicep evaluates utcNow once per deployment, so redeploys within the same month are idempotent and cross-month redeploy intentionally rolls the window.')
param startDate string = utcNow('yyyy-MM-01')

var budgetName = 'budget-condomanager-${env}'

resource budget 'Microsoft.Consumption/budgets@2023-11-01' = {
  name: budgetName
  properties: {
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: startDate
    }
    amount: monthlyAmountUsd
    // `Cost` is the only sensible category at RG scope (vs `Usage` at
    // subscription scope, which counts API calls / vCPU-hours).
    category: 'Cost'
    notifications: {
      threshold_50: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 50
        // 'Actual' = incurred spend. 'Forecasted' uses ML to predict
        // overruns from current trajectory; the AC literally says "50/80/100%
        // of monthly spend" so we match the literal wording.
        thresholdType: 'Actual'
        contactEmails: []
        contactGroups: [ actionGroupId ]
        // RG Owner role as backstop — fires even when the Action Group
        // hasn't been populated yet with operator email + Slack.
        contactRoles: [ 'Owner' ]
      }
      threshold_80: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 80
        thresholdType: 'Actual'
        contactEmails: []
        contactGroups: [ actionGroupId ]
        contactRoles: [ 'Owner' ]
      }
      threshold_100: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 100
        thresholdType: 'Actual'
        contactEmails: []
        contactGroups: [ actionGroupId ]
        contactRoles: [ 'Owner' ]
      }
    }
  }
}

output budgetName string = budget.name
output budgetId string = budget.id
