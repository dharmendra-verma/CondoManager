#!/usr/bin/env bash
# setup-github-environments.sh — (re)create the dev + prod GitHub Environments
# with the CM-15 review posture.
#
# Jira: CM-40  | Epic: CM-1 (Foundation & Azure Infrastructure)  | Phase 0
#
# The deploy pipeline (.github/workflows/deploy.yml) gates `deploy-prod` behind
# `environment: prod`. For that gate to actually pause a release, the `prod`
# environment must carry a **required reviewer** — GitHub cannot infer it from
# the workflow. `setup-azure-oidc.sh` configures the Azure side but cannot touch
# GitHub Environments, so this script is the GitHub-side counterpart.
#
# Posture (per CM-15 docs/INFRA.md "Required GitHub environments"):
#   * dev  — zero required reviewers (auto-deploy on push:main)
#   * prod — at least one required reviewer (the repo owner)
#
# Idempotent: a PUT replaces the environment's protection config, so re-running
# on an already-configured repo sets the same state (no-op effect).
#
# Prereqs:
#   * `gh` CLI authenticated as a principal with ADMIN on the repo (the owner's
#     `repo` scope suffices). The CI service principal does NOT have this — and
#     should not — so this is an operator-run, one-time (re-bootstrap) step.
#
# Usage:
#   bash infra/scripts/setup-github-environments.sh [<owner>/<repo>]
#   # defaults to dharmendra-verma/CondoManager

set -euo pipefail

REPO="${1:-dharmendra-verma/CondoManager}"
OWNER="${REPO%%/*}"

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI not found on PATH. Install: https://cli.github.com/" >&2
  exit 1
fi

# Confirm we can administer the repo before mutating anything (catches a token
# without admin/repo scope, or a typo'd repo, before a half-config).
if ! gh api "repos/$REPO" --jq '.permissions.admin' 2>/dev/null | grep -q true; then
  echo "Need ADMIN on $REPO (gh token must have 'repo' scope as an admin)." >&2
  exit 1
fi

REVIEWER_ID=$(gh api "users/$OWNER" --jq .id)
echo "Repo=$REPO  reviewer=$OWNER (id=$REVIEWER_ID)"

# dev — explicit zero required reviewers (CM-15: auto-deploy on push:main).
echo "▶  Configuring 'dev' environment (0 required reviewers)"
gh api --method PUT "repos/$REPO/environments/dev" --input - >/dev/null <<JSON
{ "reviewers": [], "deployment_branch_policy": null }
JSON
echo "   ✓ dev configured"

# prod — at least one required reviewer = the repo owner.
echo "▶  Configuring 'prod' environment (required reviewer: $OWNER)"
gh api --method PUT "repos/$REPO/environments/prod" --input - >/dev/null <<JSON
{ "reviewers": [ { "type": "User", "id": $REVIEWER_ID } ], "deployment_branch_policy": null }
JSON
echo "   ✓ prod configured"

# Verify: prod must carry a required_reviewers rule (the cheap end-to-end proof
# that the gate is live, short of publishing a real release).
echo "▶  Verifying 'prod' has a required_reviewers rule"
RULE_TYPES=$(gh api "repos/$REPO/environments/prod" --jq '.protection_rules[].type')
if printf '%s\n' "$RULE_TYPES" | grep -qx "required_reviewers"; then
  echo "   ✓ prod has required_reviewers"
else
  echo "   ✗ prod is MISSING required_reviewers (rules: ${RULE_TYPES:-none})" >&2
  exit 1
fi

echo ""
echo "Done. push:main → deploy-dev (no gate); release:published → deploy-prod"
echo "(paused at 'Waiting for review' until $OWNER approves)."
