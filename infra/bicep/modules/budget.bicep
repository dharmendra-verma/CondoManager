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
//
// ⚠️ startDate is IMMUTABLE once the budget exists (CM-104). Azure rejects any
// change to it:
//
//     400 Start date of budgets cannot be updated.
//         Please delete and create a new budget.
//
// and because this module is part of the main deployment, that 400 fails the
// ENTIRE prod deploy — including `deploy-portal-prod`, which is gated on it.
// This previously defaulted to `utcNow('yyyy-MM-01')` on the theory that a
// cross-month redeploy would "roll the window". It does not roll; it 400s. The
// effect was that every deploy from the second calendar month onward failed,
// which is exactly how it broke on 2026-07-31 and again on 2026-08-01.
//
// So: callers pass a pinned per-env literal (`budgetStartDates` in main.bicep)
// and never a computed date. Change one of those literals ONLY when you have
// deleted that env's budget and are deliberately recreating it — Azure also
// rejects a start date more than three months in the future, and a far-past one
// on creation, so the pinned value should be the month of (re)creation.

targetScope = 'resourceGroup'

@description('Deployment environment. Drives the resource name (budget-condomanager-<env>).')
@allowed([ 'dev', 'prod' ])
param env string

@description('Resource ID of the Action Group fired when a budget threshold is crossed.')
param actionGroupId string

@description('Monthly budget in USD. Operator tunes per env after first month of real usage data.')
@minValue(1)
param monthlyAmountUsd int

@description('First day of the budget window, as YYYY-MM-01. MUST be a stable constant per env — see the warning below. Required (no default) so a caller cannot silently inherit a rolling date.')
param startDate string

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
