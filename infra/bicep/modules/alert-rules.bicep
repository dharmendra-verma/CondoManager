// alert-rules.bicep — Three scheduled-query alert rules over App Insights.
// Jira: CM-26 (ACs #2, #3, #4)  | Epic: Observability  | Phase 0
//
// All three rules query the CM-22 App Insights component and fire into
// the shared CM-26 Action Group. KQL queries are inline as multi-line
// strings (Bicep allows ''' for verbatim multi-line); the same queries
// are mirrored canonically into docs/OBSERVABILITY.md so reviewers can
// read them without unpacking the Bicep AST.
//
// Severity ladder:
//   1 = page now              -> guardrail trip (cost/loop cap hit)
//   2 = page during BH        -> latency SLO breach, hallucination spike
//
// Pre-CM-28 the latency / guardrail / hallucination queries will return
// zero rows (no traffic exists). The alerts are staged infrastructure
// waiting for traffic; same posture as the CM-25 workbook panels.

targetScope = 'resourceGroup'

@description('Deployment environment. Drives the resource name (alert-*-condomanager-<env>).')
@allowed([ 'dev', 'prod' ])
param env string

@description('Azure region. Passthrough from main.bicep.')
param location string

@description('Tag map produced by tags.bicep.')
param tags object

@description('Resource ID of the CM-22 App Insights component the alerts query.')
param appInsightsId string

@description('Resource ID of the CM-26 Action Group fired on alert.')
param actionGroupId string

@description('Minimum LLM call count in the 1h window before the hallucination-spike alert can fire. Guards against the "zero traffic = zero refusal = panic" false positive. Default 10 catches phase 0; raise once steady-state prod LLM traffic is known.')
@minValue(1)
param hallucinationSpikeMinCalls int = 10

@description('Whether the three scheduled-query rules actively evaluate. Azure Monitor bills log alert rules per rule per month by evaluation frequency — these three were ~Rs. 328/month, the entire Azure Monitor line (LAW and App Insights are both on free tiers at Rs. 0). Set false to stop that spend while prod traffic is ~zero and the queries return no rows anyway. The rules stay declared here, so re-enabling is this one flag plus a deploy — no rewriting the KQL. Defaults true so the module is safe on its own; main.bicep is what turns it off.')
param rulesEnabled bool = true

// Shared action block — every rule fires the same Action Group.
var commonActions = {
  actionGroups: [ actionGroupId ]
  customProperties: {}
}

// --- Alert 1: Latency SLO (AC #2) ----------------------------------------
// Single-window p95 > 2000ms threshold over 5m. Multi-window burn-rate
// upgrade documented in docs/OBSERVABILITY.md — phase 0 doesn't have a
// formally-defined error budget so the simpler form ships first.
resource latencySlo 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-latency-slo-${env}'
  location: location
  tags: tags
  properties: {
    displayName: 'CondoManager — Latency SLO breach (p95 > 2s)'
    description: 'Fires when p95 request latency over the last 5m exceeds 2000ms. Single-window simplification of the full burn-rate SLO; multi-window upgrade documented in docs/OBSERVABILITY.md.'
    severity: 2
    enabled: rulesEnabled
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    scopes: [ appInsightsId ]
    criteria: {
      allOf: [
        {
          query: '''
            requests
            | where timestamp > ago(5m)
            | summarize p95_ms = percentile(duration, 95)
            | where p95_ms > 2000
          '''
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            minFailingPeriodsToAlert: 1
            numberOfEvaluationPeriods: 1
          }
        }
      ]
    }
    actions: commonActions
  }
}

// --- Alert 2: Guardrail trip (AC #3) -------------------------------------
// Contract: future CM-28 LangGraph stop-rules emit `guardrail.cost_cap`
// or `guardrail.loop_cap` customEvents when per-request cost or loop
// count exceeds the budget. Severity 1 (page now) because a tripped
// guardrail means real money is being spent past intended limits.
resource guardrailTrip 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-guardrail-trip-${env}'
  location: location
  tags: tags
  properties: {
    displayName: 'CondoManager — Guardrail tripped (cost or loop cap)'
    description: 'Fires when any customEvent named guardrail.cost_cap or guardrail.loop_cap is emitted in the last 5m. CM-28 will emit these from the LangGraph stop-rules; until then this alert is staged infrastructure waiting for traffic.'
    severity: 1
    enabled: rulesEnabled
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    scopes: [ appInsightsId ]
    criteria: {
      allOf: [
        {
          query: '''
            customEvents
            | where timestamp > ago(5m)
            | where name in ('guardrail.cost_cap', 'guardrail.loop_cap')
            | summarize trip_count = count() by name
            | where trip_count > 0
          '''
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            minFailingPeriodsToAlert: 1
            numberOfEvaluationPeriods: 1
          }
        }
      ]
    }
    actions: commonActions
  }
}

// --- Alert 3: Hallucination spike (AC #4) --------------------------------
// "Refusal rate drops sharply" is the AC; literal interpretation = a
// floor on refusal_pct. Pure floor would false-positive on zero traffic
// (no calls = no refusals = "panic"), so we gate on a minimum call
// count. Param-driven so prod can raise the floor once steady-state
// traffic is known.
//
// The `iff(total == 0, 100.0, ...)` clause inside the KQL is a belt-
// and-suspenders defense: even if hallucinationSpikeMinCalls=0 somehow
// slipped past the @minValue guard, the alert won't fire on zero traffic.
resource hallucinationSpike 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-hallucination-spike-${env}'
  location: location
  tags: tags
  properties: {
    displayName: 'CondoManager — Hallucination spike (refusal rate floor)'
    description: 'Fires when total LLM calls in the last 1h > ${hallucinationSpikeMinCalls} AND refusal rate < 1%. Refusals are tracked via the llm.refused customEvent (CM-30 Triage emits when the model declines). Traffic gate prevents the "zero traffic = zero refusal" false positive.'
    severity: 2
    enabled: rulesEnabled
    evaluationFrequency: 'PT15M'
    windowSize: 'PT1H'
    scopes: [ appInsightsId ]
    criteria: {
      allOf: [
        {
          // NOTE: Bicep does NOT interpolate ${...} inside ''' multi-line
          // strings — they are verbatim. A literal `${hallucinationSpikeMinCalls}`
          // shipped to Azure makes KQL fail with "Query could not be parsed at
          // '{'". Use format() with a {0} placeholder to splice the threshold in.
          query: format('''
            let win = 1h;
            let llm = dependencies
              | where timestamp > ago(win)
              | where customDimensions['openinference.span.kind'] == 'LLM'
                 or customDimensions['gen_ai.system'] == 'openai';
            let refusals = customEvents
              | where timestamp > ago(win)
              | where name == 'llm.refused';
            let total = toscalar(llm | count);
            let refused = toscalar(refusals | count);
            print
              total = total,
              refused = refused,
              refusal_pct = iff(total == 0, 100.0, todouble(refused) / total * 100)
            | where total > {0} and refusal_pct < 1.0
          ''', hallucinationSpikeMinCalls)
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            minFailingPeriodsToAlert: 1
            numberOfEvaluationPeriods: 1
          }
        }
      ]
    }
    actions: commonActions
  }
}

output latencySloRuleName string = latencySlo.name
output guardrailTripRuleName string = guardrailTrip.name
output hallucinationSpikeRuleName string = hallucinationSpike.name
