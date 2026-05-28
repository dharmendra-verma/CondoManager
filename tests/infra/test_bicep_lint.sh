#!/usr/bin/env bash
# Bicep lint test — CM-15 (single-RG topology) + CM-16 (Container Apps modules)
# Verifies:
#   1. Bicep templates compile cleanly (no syntax/type errors)
#   2. main.bicep is resource-group scoped (RG is bootstrapped out-of-band)
#   3. tags.bicep declares the full 5-tag schema for downstream resources
#   4. tags.bicep restricts env to dev / prod / shared (no rogue env names)
#   5. main.parameters.json exists, is valid JSON, and declares env
#   6. workflow targets rg-condomanager via `az deployment group …`
#   7. (CM-16) all module files in infra/bicep/modules/ compile and are RG-scoped
#   8. (CM-16) per-env modules restrict env to dev / prod
#   9. (CM-16) Container Apps env uses the Consumption workload profile
#  10. (CM-16) VNet subnet is delegated to Microsoft.App/environments
#  11. (CM-16) Container App defaults to minReplicas: 0 (free-tier guard)
#  12. (CM-16) compiled ARM contains the Container Apps + VNet resource types
#
# Run locally:  bash tests/infra/test_bicep_lint.sh
# Run in CI:    invoked by .github/workflows/infra-deploy.yml

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BICEP_DIR="$ROOT/infra/bicep"
MODULES_DIR="$BICEP_DIR/modules"
FAIL=0

# Resolve the Bicep CLI. CI installs the standalone binary at /usr/local/bin/bicep
# (see .github/workflows/infra-deploy.yml). On dev machines without the standalone
# binary we fall back to `az bicep`, which uses the same engine.
if command -v bicep >/dev/null 2>&1; then
  BICEP_BIN="standalone"
  echo "▶  Bicep CLI version: $(bicep --version)"
elif command -v az >/dev/null 2>&1 && az bicep version >/dev/null 2>&1; then
  BICEP_BIN="az"
  echo "▶  Bicep CLI (via az): $(az bicep version 2>&1 | tail -n1)"
else
  echo "❌ Neither standalone 'bicep' nor 'az bicep' is available. Install one before running the lint."
  exit 1
fi

bicep_build() {
  local src="$1"
  local out="$2"
  if [ "$BICEP_BIN" = "standalone" ]; then
    bicep build "$src" --outfile "$out"
  else
    az bicep build --file "$src" --outfile "$out" >/dev/null
  fi
}

echo "▶  Compiling main.bicep → ARM"
bicep_build "$BICEP_DIR/main.bicep" /tmp/main.json
echo "   ✓ main.bicep compiles cleanly"

echo "▶  Compiling tags.bicep → ARM"
bicep_build "$BICEP_DIR/tags.bicep" /tmp/tags.json
echo "   ✓ tags.bicep compiles cleanly"

echo "▶  Compiling CM-16 modules in $MODULES_DIR"
MODULES=("vnet" "log-analytics" "container-apps-env" "container-app")
for m in "${MODULES[@]}"; do
  if [ ! -f "$MODULES_DIR/$m.bicep" ]; then
    echo "   ✗ module $m.bicep MISSING"
    FAIL=1
    continue
  fi
  bicep_build "$MODULES_DIR/$m.bicep" "/tmp/$m.json"
  echo "   ✓ $m.bicep compiles cleanly"
done

REQUIRED_TAGS=("env" "owner" "cost-center" "project" "managed-by")
echo "▶  Verifying required tags exist in tags.bicep (for downstream resources)"
for tag in "${REQUIRED_TAGS[@]}"; do
  if grep -Fq "$tag" "$BICEP_DIR/tags.bicep"; then
    echo "   ✓ tag '$tag' present"
  else
    echo "   ✗ tag '$tag' MISSING from tags.bicep"
    FAIL=1
  fi
done

echo "▶  Verifying targetScope is resourceGroup in main.bicep and all modules"
for f in "$BICEP_DIR/main.bicep" "$MODULES_DIR/vnet.bicep" "$MODULES_DIR/log-analytics.bicep" "$MODULES_DIR/container-apps-env.bicep" "$MODULES_DIR/container-app.bicep"; do
  if grep -Fq "targetScope = 'resourceGroup'" "$f"; then
    echo "   ✓ $(basename "$f") is resource-group scoped"
  else
    echo "   ✗ $(basename "$f") is NOT resource-group scoped — RG-scoped SP cannot deploy it"
    FAIL=1
  fi
done

echo "▶  Verifying workflow targets rg-condomanager via 'az deployment group'"
WF="$ROOT/.github/workflows/infra-deploy.yml"
if grep -Fq "az deployment group" "$WF" && grep -Fq "rg-condomanager" "$WF"; then
  echo "   ✓ workflow uses RG-scoped deployment commands"
else
  echo "   ✗ workflow does not target rg-condomanager via 'az deployment group'"
  FAIL=1
fi

echo "▶  Verifying tags.bicep restricts env to dev/prod/shared"
if grep -Eq "@allowed\\(\\s*\\[\\s*'dev',\\s*'prod',\\s*'shared'\\s*\\]" "$BICEP_DIR/tags.bicep"; then
  echo "   ✓ env in tags.bicep is constrained to dev/prod/shared"
else
  echo "   ✗ env @allowed list in tags.bicep does not match dev/prod/shared"
  FAIL=1
fi

echo "▶  Verifying per-env modules restrict env to dev/prod (no 'shared')"
for m in "${MODULES[@]}"; do
  if grep -Eq "@allowed\\(\\s*\\[\\s*'dev',\\s*'prod'\\s*\\]" "$MODULES_DIR/$m.bicep"; then
    echo "   ✓ env in $m.bicep is constrained to dev/prod"
  else
    echo "   ✗ env @allowed list in $m.bicep does not match dev/prod"
    FAIL=1
  fi
done

echo "▶  Verifying Container Apps env uses Consumption workload profile"
if grep -Fq "'Consumption'" "$MODULES_DIR/container-apps-env.bicep" \
   && grep -Fq "workloadProfileType: 'Consumption'" "$MODULES_DIR/container-apps-env.bicep"; then
  echo "   ✓ Consumption workload profile present (free-tier eligible)"
else
  echo "   ✗ Consumption workload profile missing from container-apps-env.bicep"
  FAIL=1
fi

echo "▶  Verifying VNet subnet is delegated to Microsoft.App/environments"
if grep -Fq "serviceName: 'Microsoft.App/environments'" "$MODULES_DIR/vnet.bicep"; then
  echo "   ✓ subnet delegation to Microsoft.App/environments present"
else
  echo "   ✗ VNet subnet is NOT delegated to Microsoft.App/environments — Container Apps will fail to deploy"
  FAIL=1
fi

echo "▶  Verifying Container App defaults to minReplicas 0 (free-tier guard)"
if grep -Eq "param\\s+minReplicas\\s+int\\s*=\\s*0" "$MODULES_DIR/container-app.bicep"; then
  echo "   ✓ minReplicas defaults to 0 (scale-to-zero when idle)"
else
  echo "   ✗ minReplicas does NOT default to 0 in container-app.bicep — could burn vCPU-seconds"
  FAIL=1
fi

echo "▶  Verifying compiled main.json includes Container Apps + VNet resource types"
for type in "Microsoft.Network/virtualNetworks" "Microsoft.OperationalInsights/workspaces" "Microsoft.App/managedEnvironments" "Microsoft.App/containerApps"; do
  if grep -Fq "$type" /tmp/main.json; then
    echo "   ✓ $type present in compiled ARM"
  else
    echo "   ✗ $type MISSING from compiled ARM"
    FAIL=1
  fi
done

echo "▶  Verifying main.parameters.json exists, is valid JSON, and declares env"
pfile="$BICEP_DIR/main.parameters.json"
if [ -f "$pfile" ]; then
  echo "   ✓ $pfile present"
  # Prefer python3 (Linux/CI); fall back to python (Windows Git Bash).
  PY=""
  for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c "import json" >/dev/null 2>&1; then
      PY="$cand"; break
    fi
  done
  if [ -z "$PY" ]; then
    echo "   ✗ no working python interpreter found to validate JSON"
    FAIL=1
  elif "$PY" -c "import json,sys; json.load(open(sys.argv[1]))" "$pfile" 2>/dev/null; then
    echo "   ✓ valid JSON"
  else
    echo "   ✗ INVALID JSON"
    FAIL=1
  fi
  if "$PY" -c "import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if 'env' in d.get('parameters', {}) else 1)" "$pfile" 2>/dev/null; then
    echo "   ✓ 'env' parameter declared"
  else
    echo "   ✗ 'env' parameter missing from main.parameters.json"
    FAIL=1
  fi
else
  echo "   ✗ $pfile missing"
  FAIL=1
fi

echo "▶  Verifying no leftover per-env parameter files"
for env in dev staging prod; do
  if [ -f "$BICEP_DIR/main.parameters.$env.json" ]; then
    echo "   ✗ stale file $BICEP_DIR/main.parameters.$env.json still present (single-RG topology uses one main.parameters.json)"
    FAIL=1
  fi
done
echo "   ✓ no stale per-env parameter files"

if [ $FAIL -ne 0 ]; then
  echo ""
  echo "❌ Lint test FAILED"
  exit 1
fi
echo ""
echo "✅ All Bicep lint checks passed"
