#!/usr/bin/env bash
# Bicep lint test — CM-15 (single-RG topology) + CM-16 (Container Apps modules) + CM-17 (Cosmos DB)
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
#  13. (CM-17) cosmos.bicep enables EnableNoSQLVectorSearch + diskANN on
#      policies-vector, declares the four required containers, defaults
#      enableFreeTier to true, and is wired into main.bicep
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

echo "▶  Compiling per-resource modules in $MODULES_DIR"
MODULES=("vnet" "log-analytics" "container-apps-env" "container-app" "cosmos")
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
for f in "$BICEP_DIR/main.bicep" "$MODULES_DIR/vnet.bicep" "$MODULES_DIR/log-analytics.bicep" "$MODULES_DIR/container-apps-env.bicep" "$MODULES_DIR/container-app.bicep" "$MODULES_DIR/cosmos.bicep"; do
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

echo "▶  Verifying compiled main.json includes Container Apps, VNet, and Cosmos resource types"
for type in "Microsoft.Network/virtualNetworks" "Microsoft.OperationalInsights/workspaces" "Microsoft.App/managedEnvironments" "Microsoft.App/containerApps" "Microsoft.DocumentDB/databaseAccounts"; do
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

# ---------------------------------------------------------------------------
# CM-17 — Cosmos DB module checks
# ---------------------------------------------------------------------------

COSMOS="$MODULES_DIR/cosmos.bicep"

echo "▶  Verifying cosmos.bicep enables the NoSQL vector-search capability"
if grep -Fq "EnableNoSQLVectorSearch" "$COSMOS"; then
  echo "   ✓ EnableNoSQLVectorSearch capability declared"
else
  echo "   ✗ EnableNoSQLVectorSearch capability MISSING — vector indexes won't be accepted"
  FAIL=1
fi

echo "▶  Verifying cosmos.bicep defines a diskANN vector index"
if grep -Fq "diskANN" "$COSMOS"; then
  echo "   ✓ diskANN vector index present"
else
  echo "   ✗ diskANN vector index MISSING from cosmos.bicep"
  FAIL=1
fi

echo "▶  Verifying cosmos.bicep declares all four required containers"
REQUIRED_CONTAINERS=("tenants" "tickets" "conversations" "policies-vector")
for c in "${REQUIRED_CONTAINERS[@]}"; do
  # Match the container resource id: `id: 'tenants'`, `id: 'policies-vector'`, …
  if grep -Eq "id:[[:space:]]+'$c'" "$COSMOS"; then
    echo "   ✓ container '$c' declared"
  else
    echo "   ✗ container '$c' MISSING from cosmos.bicep"
    FAIL=1
  fi
done

echo "▶  Verifying cosmos.bicep enables the free tier by default"
if grep -Eq "param enableFreeTier bool = true" "$COSMOS"; then
  echo "   ✓ enableFreeTier defaults to true"
else
  echo "   ✗ enableFreeTier default is not true — AC requires free-tier-by-default"
  FAIL=1
fi

echo "▶  Verifying policies-vector container has dedicated throughput (vector indexing forbids shared throughput)"
if grep -Eq "param vectorContainerThroughput" "$COSMOS" \
   && grep -Eq "throughput:[[:space:]]+vectorContainerThroughput" "$COSMOS"; then
  echo "   ✓ vectorContainerThroughput param wired into a container's options.throughput"
else
  echo "   ✗ policies-vector lacks dedicated throughput — Cosmos rejects vector indexing on shared-throughput containers"
  FAIL=1
fi

echo "▶  Verifying combined throughput (database shared + vector dedicated) stays within free-tier 1000 RU/s"
DB_RU=$(grep -Eo "param databaseThroughput int = [0-9]+" "$COSMOS" | grep -Eo "[0-9]+$" || echo "0")
VEC_RU=$(grep -Eo "param vectorContainerThroughput int = [0-9]+" "$COSMOS" | grep -Eo "[0-9]+$" || echo "0")
TOTAL_RU=$((DB_RU + VEC_RU))
if [ "$TOTAL_RU" -le 1000 ] && [ "$DB_RU" -gt 0 ] && [ "$VEC_RU" -gt 0 ]; then
  echo "   ✓ databaseThroughput=$DB_RU + vectorContainerThroughput=$VEC_RU = ${TOTAL_RU} RU/s (≤ 1000 free-tier ceiling)"
else
  echo "   ✗ combined defaults exceed free-tier ceiling: databaseThroughput=$DB_RU + vectorContainerThroughput=$VEC_RU = ${TOTAL_RU} RU/s"
  FAIL=1
fi

echo "▶  Verifying main.bicep wires in the cosmos module"
if grep -Fq "modules/cosmos.bicep" "$BICEP_DIR/main.bicep"; then
  echo "   ✓ main.bicep references modules/cosmos.bicep"
else
  echo "   ✗ main.bicep does NOT reference modules/cosmos.bicep"
  FAIL=1
fi

if [ $FAIL -ne 0 ]; then
  echo ""
  echo "❌ Lint test FAILED"
  exit 1
fi
echo ""
echo "✅ All Bicep lint checks passed"
