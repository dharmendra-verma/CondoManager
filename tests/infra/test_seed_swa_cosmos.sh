#!/usr/bin/env bash
# test_seed_swa_cosmos.sh — behavioural test for seed-swa-cosmos-setting.sh (CM-64).
#
# Stubs the `az` CLI on PATH so this runs offline and version-independently,
# and asserts the script's contract — the part a static grep cannot prove:
#   1. KV secret = REPLACE-ME       -> non-zero exit, NO `appsettings set` call
#   2. KV secret empty/missing      -> non-zero exit, NO `appsettings set` call
#   3. real value, setting differs  -> exactly one `appsettings set` with the value
#   4. real value, setting matches  -> exit 0, NO `appsettings set` call (idempotent)
#
# The stub records every `az` invocation to $AZ_LOG and returns canned output
# driven by $STUB_KV_VALUE / $STUB_SWA_VALUE / $STUB_KV_MISSING so each scenario
# is pure.
#
# Run locally:  bash tests/infra/test_seed_swa_cosmos.sh
# Run in CI:    invoked by .github/workflows/build.yml (lint-infra job)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$ROOT/infra/scripts/seed-swa-cosmos-setting.sh"
FAIL=0

echo "▶  Behavioural tests for seed-swa-cosmos-setting.sh (CM-64)"

if [ ! -s "$SCRIPT" ]; then
  echo "   ✗ $SCRIPT missing or empty"
  exit 1
fi

# A representative Cosmos connection string (fake key) — exercises the '='/';'
# characters that must survive being passed as a single --setting-names arg.
REAL="AccountEndpoint=https://cosmos-condomanager-prod.documents.azure.com:443/;AccountKey=ZmFrZWtleQ==;"

# Temp dir holding a fake `az` placed earlier on PATH.
STUBDIR="$(mktemp -d)"
trap 'rm -rf "$STUBDIR"' EXIT
export AZ_LOG="$STUBDIR/az.log"

cat > "$STUBDIR/az" <<'STUB'
#!/usr/bin/env bash
# Fake az: log the full arg string, emit canned output per subcommand.
echo "$*" >> "$AZ_LOG"
case "$*" in
  *"keyvault secret show"*)
    # STUB_KV_MISSING=1 behaves like a missing secret (non-zero, no output).
    if [ "${STUB_KV_MISSING:-0}" = "1" ]; then exit 1; fi
    printf '%s' "${STUB_KV_VALUE:-}"
    ;;
  *"staticwebapp appsettings list"*)
    printf '%s' "${STUB_SWA_VALUE:-}"
    ;;
  *"staticwebapp appsettings set"*)
    : # success, no output
    ;;
  *)
    : # any other az call: succeed quietly
    ;;
esac
exit 0
STUB
chmod +x "$STUBDIR/az"

assert_no_set() {
  if grep -q "appsettings set" "$AZ_LOG"; then
    echo "   ✗ $1: expected NO 'appsettings set', but it was called"; FAIL=1
  else
    echo "   ✓ $1: no 'appsettings set' call"
  fi
}

assert_set_with() {
  if grep -q "appsettings set" "$AZ_LOG" && grep -Fq "$2" "$AZ_LOG"; then
    echo "   ✓ $1: 'appsettings set' called carrying the value"
  else
    echo "   ✗ $1: expected 'appsettings set' carrying the connection string"; FAIL=1
  fi
}

# 1. Placeholder → fail-closed.
: > "$AZ_LOG"
if PATH="$STUBDIR:$PATH" STUB_KV_VALUE="REPLACE-ME" bash "$SCRIPT" prod >/dev/null 2>&1; then
  echo "   ✗ placeholder: script exited 0 (expected non-zero)"; FAIL=1
else
  echo "   ✓ placeholder: script exited non-zero"
fi
assert_no_set "placeholder"

# 2. Empty / missing KV secret → fail-closed.
: > "$AZ_LOG"
if PATH="$STUBDIR:$PATH" STUB_KV_MISSING=1 bash "$SCRIPT" prod >/dev/null 2>&1; then
  echo "   ✗ missing-secret: script exited 0 (expected non-zero)"; FAIL=1
else
  echo "   ✓ missing-secret: script exited non-zero"
fi
assert_no_set "missing-secret"

# 3. Real value, SWA setting currently different/unset → one set with the value.
: > "$AZ_LOG"
PATH="$STUBDIR:$PATH" STUB_KV_VALUE="$REAL" STUB_SWA_VALUE="" bash "$SCRIPT" prod >/dev/null 2>&1
assert_set_with "real-value-new" "$REAL"

# 4. Real value already present on the SWA → idempotent no-op.
: > "$AZ_LOG"
PATH="$STUBDIR:$PATH" STUB_KV_VALUE="$REAL" STUB_SWA_VALUE="$REAL" bash "$SCRIPT" prod >/dev/null 2>&1
assert_no_set "idempotent-match"

# 5. Bad env arg → usage error.
: > "$AZ_LOG"
if PATH="$STUBDIR:$PATH" bash "$SCRIPT" staging >/dev/null 2>&1; then
  echo "   ✗ bad-env: script exited 0 (expected non-zero)"; FAIL=1
else
  echo "   ✓ bad-env: rejected non dev/prod env"
fi

if [ "$FAIL" -ne 0 ]; then
  echo ""
  echo "❌ test_seed_swa_cosmos.sh FAILED"
  exit 1
fi
echo ""
echo "✅ seed-swa-cosmos-setting.sh behavioural checks passed"
