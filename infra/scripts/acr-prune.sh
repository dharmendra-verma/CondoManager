#!/usr/bin/env bash
# acr-prune.sh — Basic-SKU equivalent of ACR's Premium retention policy.
# Keeps the latest N tagged manifests per repo (default 5) and deletes
# untagged manifests. Idempotent; safe to re-run.
#
# Jira: CM-20  | Epic: CM-1 (Foundation & Azure Infrastructure)  | Phase 0
#
# Algorithm:
#   1. List manifests for <repo> ordered by createdTime descending.
#   2. Keep the first KEEP_LAST that carry at least one tag.
#   3. Delete every other manifest by digest (covers older tagged AND all
#      untagged manifests — no consumer can pin to an untagged digest).
#
# Usage:
#   bash infra/scripts/acr-prune.sh <acr-name> <repo> [keep_last]
#   bash infra/scripts/acr-prune.sh acrcondomanagerdev base/python 5
#
# Prereqs:
#   * az CLI logged in (`az login` or workflow OIDC)
#   * The caller has `AcrDelete` on the registry (the OIDC SP gets this
#     transitively via `AcrPush`; for manual ops, grant explicitly).

set -euo pipefail

ACR="${1:?usage: $0 <acr-name> <repo> [keep_last]}"
REPO="${2:?usage: $0 <acr-name> <repo> [keep_last]}"
KEEP="${3:-5}"

if ! command -v az >/dev/null 2>&1; then
  echo "az CLI not found on PATH. Install: https://learn.microsoft.com/cli/azure/install-azure-cli" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
  echo "python3 or python required (used for JSON parsing)." >&2
  exit 1
fi
PY=$(command -v python3 || command -v python)

# Empty repo / repo-doesn't-exist → nothing to do. `show-manifests` exits 0
# with [] when the repo is empty, so the script will fall through cleanly.
echo "Pruning $ACR/$REPO (keep last $KEEP tagged; delete untagged)"
TAGS_JSON=$(az acr repository show-manifests \
  --name "$ACR" --repository "$REPO" \
  --orderby time_desc \
  --query '[].{digest:digest,tags:tags,timestamp:timestamp}' -o json 2>/dev/null || echo '[]')

if [ "$TAGS_JSON" = "[]" ] || [ -z "$TAGS_JSON" ]; then
  echo "  no manifests in $ACR/$REPO — nothing to prune"
  exit 0
fi

# Walk in Python: easier than parsing JSON with bash. Emits delete digests
# one per line on stdout, then `xargs` shells them out to `az acr repository
# delete`. We deliberately do NOT pass --yes via stdin to avoid an arg-list
# overflow on repos with thousands of manifests; loop one-by-one instead.
DELETE_DIGESTS=$(
  printf '%s' "$TAGS_JSON" | "$PY" -c "
import json, sys
manifests = json.load(sys.stdin)
keep = 0
keep_last = $KEEP
for m in manifests:
    if m.get('tags') and keep < keep_last:
        keep += 1
        continue
    print(m['digest'])
"
)

if [ -z "$DELETE_DIGESTS" ]; then
  echo "  manifest count <= KEEP_LAST ($KEEP) and no untagged — nothing to delete"
  exit 0
fi

DELETED=0
while IFS= read -r digest; do
  [ -z "$digest" ] && continue
  echo "  - deleting $REPO@$digest"
  az acr repository delete --name "$ACR" --image "${REPO}@${digest}" --yes --output none
  DELETED=$((DELETED + 1))
done <<< "$DELETE_DIGESTS"

echo "Done. deleted=$DELETED"
