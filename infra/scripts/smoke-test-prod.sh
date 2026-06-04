#!/usr/bin/env bash
# smoke-test-prod.sh — end-to-end prod smoke test for the agent runtime. CM-59.
#
# Confirms a tenant message flows tenant -> normalize -> triage -> agent ->
# response through the live Container App. Hits three endpoints on the
# agent-runtime image (replacing the old hello-world placeholder):
#
#   GET  /healthz       -> 200, always (ungated liveness probe)
#   POST /web/login     -> resolves a TEST tenant by mobile number
#   POST /web/message   -> runs the pipeline, returns a triaged reply
#
# The /web/* endpoints only respond when WEBCHAT_TEST_ENABLED=1 is set on the
# Container App (container-app.bicep wires it for the real agent image). If they
# 404, the app is still the hello-world shell or the channel flag is off.
#
# Usage:
#   az login           # needs reader on rg-condomanager to resolve the FQDN
#   bash infra/scripts/smoke-test-prod.sh
#
# Override the resource names with env vars if they ever change:
#   RG, CONTAINER_APP, TEST_MOBILE
set -euo pipefail

RG="${RG:-rg-condomanager}"
CONTAINER_APP="${CONTAINER_APP:-ca-hello-condomanager-prod}"
# A hardcoded CM-55 test tenant (Asha Rao). These are throwaway test creds — see
# agents/webchat/tenants.py. No real PII.
TEST_MOBILE="${TEST_MOBILE:-+919876543210}"
TEST_MESSAGE="${TEST_MESSAGE:-The kitchen tap in unit 4B has been leaking since this morning.}"

echo "▶  Resolving Container App ingress FQDN ($CONTAINER_APP)…"
FQDN="$(az containerapp show --name "$CONTAINER_APP" --resource-group "$RG" \
          --query 'properties.configuration.ingress.fqdn' -o tsv)"
if [ -z "$FQDN" ]; then
  echo "   ✗ Could not resolve FQDN — is the Container App deployed?" >&2
  exit 1
fi
BASE="https://${FQDN}"
echo "   ✓ $BASE"

fail() { echo "   ✗ $1" >&2; exit 1; }

echo "▶  GET /healthz (liveness)…"
HEALTH="$(curl -fsS "${BASE}/healthz")" || fail "/healthz did not return 2xx — app not serving"
echo "   ✓ $HEALTH"
case "$HEALTH" in
  *'"status":"ok"'*) : ;;
  *) fail "/healthz body missing status=ok" ;;
esac
case "$HEALTH" in
  *'"channel_enabled":true'*) : ;;
  *) fail "channel_enabled is not true — WEBCHAT_TEST_ENABLED is off, /web/* will 404" ;;
esac

echo "▶  POST /web/login (resolve test tenant)…"
LOGIN="$(curl -fsS -X POST "${BASE}/web/login" \
          -H 'content-type: application/json' \
          -d "{\"mobile\":\"${TEST_MOBILE}\"}")" \
  || fail "/web/login failed — channel disabled or tenant unknown"
echo "   ✓ $LOGIN"

echo "▶  POST /web/message (run the agent pipeline)…"
MSG="$(curl -fsS -X POST "${BASE}/web/message" \
        -H 'content-type: application/json' \
        -d "{\"mobile\":\"${TEST_MOBILE}\",\"content\":\"${TEST_MESSAGE}\"}")" \
  || fail "/web/message failed — pipeline error"
echo "   ✓ $MSG"
case "$MSG" in
  *'"reply"'*) : ;;
  *) fail "/web/message response missing a reply field" ;;
esac

echo
echo "✅ Prod smoke test PASSED — tenant message flowed through the live pipeline."
echo
echo "Next (manual): confirm the trace landed in Application Insights. In the"
echo "Azure Portal open appi-condomanager-prod > Logs and run:"
echo
echo "    requests"
echo "    | where timestamp > ago(15m)"
echo "    | where url endswith \"/web/message\""
echo "    | project timestamp, name, resultCode, duration"
echo "    | order by timestamp desc"
