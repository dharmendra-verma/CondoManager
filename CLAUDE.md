# CLAUDE.md — Coding Agent Briefing

> This file is read automatically by Claude Code at the start of every session.
> Everything in it is **pre-decided context**. The agent must NOT re-ask the
> user for any of it. The autonomy contract is in `AGENT_RULES.md`.

---

## 1. Project identity

| | |
|---|---|
| **Name** | CondoManager |
| **Owner** | Dharmendra Verma (`to.dharmendra.verma@gmail.com`) |
| **What** | Multi-agent platform for condominium maintenance & inquiry management. LangGraph orchestrator routes tenant messages (WhatsApp / Telegram / email) to specialist agents (Triage, Maintenance, Knowledge, Escalation, Vendor, Analytics). |
| **Status** | Phase 0 — foundation. Infra story CM-15 in flight. |

## 2. Source of truth

| Concern | Location |
|---|---|
| **Stories / sprints** | Jira project `CM` at `https://projecttracking.atlassian.net` (cloud id `ba95f5fc-5994-47bc-81e4-161f6a62e829`). Use the Atlassian MCP. |
| **Code** | `https://github.com/dharmendra-verma/CondoManager` (private). Default branch `main`. |
| **CI/CD** | GitHub Actions (`.github/workflows/`). OIDC federated credentials, secrets `AZURE_CLIENT_ID` / `AZURE_TENANT_ID` / `AZURE_SUBSCRIPTION_ID`. |
| **Docs** | `docs/` — start with `docs/INFRA.md`. |
| **Project memory** | `.sdlc/memory/projects/CM.json` in the workspace (not committed to repo). |

## 3. Decisions already made — do NOT re-ask

These are settled. If the user wants to change one, they'll bring it up.

### Topology
- **One shared Azure Resource Group**: `rg-condomanager` hosts both dev and prod workloads.
- **Two deployment stages**: `dev` and `prod`. No `staging`.
- **Region**: `eastus2` for everything.

### Naming
- **RG**: `rg-condomanager` (shared).
- **Per-env resources**: `<resource>-condomanager-<env>` — e.g. `cosmos-condomanager-dev`, `kv-condomanager-prod`.
- **Branches**: `feature/<JIRA-KEY>-<short-slug>` — e.g. `feature/CM-16-cosmos-db`.
- **Commits**: [Conventional Commits](https://www.conventionalcommits.org/) prefixed by the Jira key:
  - `feat(<area>): CM-NN <one-line summary>`
  - `fix(<area>): CM-NN <one-line summary>`
  - `refactor(<area>): CM-NN <one-line summary>`
  - `test(<area>): CM-NN <one-line summary>`
  - `docs(<area>): CM-NN <one-line summary>`
- **PR titles**: must include the Jira key (e.g. `CM-16: Provision Cosmos DB with DiskANN`).

### Tagging schema (every resource MUST carry all five)
| Tag | RG value | Per-env value |
|---|---|---|
| `env` | `shared` | `dev` or `prod` |
| `owner` | `platform-team` | `platform-team` |
| `cost-center` | `cc-condomanager` | `cc-condomanager` |
| `project` | `condo-manager` | `condo-manager` |
| `managed-by` | `bicep` | `bicep` |

### Tech stack
- **IaC**: Bicep (subscription scope for RG; resource-group scope for everything else).
- **Agents**: Python + Pydantic + LangGraph.
- **Data**: Cosmos DB (DiskANN vector + docs) + Azure Key Vault.
- **Runtime**: Azure Container Apps + Azure Container Registry.
- **Observability**: LangSmith + Langfuse + OpenTelemetry → App Insights + Azure Monitor.
- **Channels**: Twilio WhatsApp, Telegram Bot API, IMAP email.
- **Portal**: TypeScript (planned, later phase).

### Definition of Done — applies to every story
1. Code committed on `feature/<JIRA-KEY>-<slug>`.
2. All existing tests still pass.
3. New behavior covered by tests where applicable.
4. Lint test in `tests/infra/test_bicep_lint.sh` (or area equivalent) passes locally before push.
5. PR opened against `main` with the Jira key in the title.
6. GitHub Actions CI green on the PR.
7. Self code-review per the 7-point SDLC checklist (see §6).
8. Jira story commented with a deliverables summary + diffstat.
9. Jira transitioned to `Done` ONLY after the user confirms (see `AGENT_RULES.md`).

## 4. Sprint / story workflow

Detected from the user's message:

| User says... | Agent action |
|---|---|
| "Start CM-NN" / "Pick up the next story" / "Implement CM-NN" | EXECUTE: transition to In Progress, create feature branch, implement, test, commit, open PR |
| "What's the status?" / "Show me the board" | STATUS: query Jira for open CM stories, print a table |
| "Review the PR" / "Check the code" | REVIEW: run the 7-point checklist, report PASS / NEEDS CHANGES |
| "Ship CM-NN" / "Close it" / "Mark done" | CLOSE: transition to Done, add closing comment, check sprint completion |

## 5. Repository layout (current, will grow)

```
.
├── infra/                # Bicep IaC
│   ├── bicep/
│   │   ├── main.bicep              # subscription-scope, creates the shared RG
│   │   ├── tags.bicep              # resource-group-scope tag schema for downstream
│   │   └── main.parameters.json
│   └── (future: cosmos/, keyvault/, acr/, containerapps/, etc.)
├── .github/workflows/    # CI pipelines
├── tests/                # Per-area test scripts
├── docs/                 # Operator docs
├── agents/               # (future) LangGraph agents in Python
├── portal/               # (future) TypeScript tenant + manager UI
├── CLAUDE.md             # this file
└── AGENT_RULES.md        # autonomy contract
```

## 6. Self code-review — 7-point checklist (mandatory before opening PR)

1. **Correctness** — does the code satisfy every Jira AC?
2. **Security** — secrets? injection vectors? over-permissive RBAC? federated creds used?
3. **Performance** — N+1, unbounded loops, missing indexes?
4. **Error handling** — failures handled? `set -euo pipefail` in shell? no swallowed exceptions?
5. **Test coverage** — meaningful asserts? edge cases? CI runs the same tests as local?
6. **Code quality** — DRY, naming, comments on Bicep params, type safety in Python?
7. **Backward compatibility** — does this break any existing CI job or deployed resource?

Report verdict: **PASS** / **PASS WITH NOTES** / **NEEDS CHANGES** in the PR description.

## 7. Useful one-liners

```bash
# Run the infra lint test (Bicep CLI must be on PATH)
bash tests/infra/test_bicep_lint.sh

# Compile main.bicep to ARM (for debugging)
bicep build infra/bicep/main.bicep --outfile /tmp/main.json

# Manual smoke-test deploy (requires `az login`)
az deployment sub create \
  --name cm-rg-manual \
  --location eastus2 \
  --template-file infra/bicep/main.bicep \
  --parameters infra/bicep/main.parameters.json
```

## 8. Jira API quick reference

```
cloudId:   ba95f5fc-5994-47bc-81e4-161f6a62e829
projectKey: CM

# Find all open stories ordered by rank
searchJiraIssuesUsingJql:
  jql: "project = CM AND statusCategory != Done ORDER BY rank ASC"

# Transition IDs (per CM workflow):
#   11 → To Do
#   21 → In Progress
#   31 → Done
```

---

## 9. Planner / Dev two-agent workflow (project-specific)

The user has set up a file-based hand-off between two agent roles. The full
workflow doc lives in `<workspace>/Planning/README.md` (outside the repo
because it's per-developer workflow state). **Read it once at session start
when working in this project.**

### Folder structure (in the user's workspace, NOT the repo)

```
<workspace>/Planning/
├── planned/        ← Planner Agent writes stories here
├── inprogress/     ← Dev Agent moves them here when starting work
├── completed/      ← Dev Agent moves them here after successful push
└── _template/story-plan-template.md
```

The same `<JIRA-KEY>-<slug>.md` file physically moves between these folders
as its `status:` frontmatter field changes.

### Trigger-phrase routing

| User says... | Role | Action |
|---|---|---|
| "Plan for next story" / "Plan the next story" / "What's next to plan" | **Planner** | Find top-ranked `To Do` story in current sprint → confirm sprint membership → transition to `In Progress` → produce plan → write `Planning/planned/<file>.md` → Jira comment with plan → stop |
| "Start implementing the story from planned folder" / "Implement the next planned story" / "Start dev" | **Dev** | Pick oldest file in `Planning/planned/` (or named CM-NN) → `mv` to `inprogress/` → implement → test → commit → push → open PR → `mv` to `completed/` → report |

### Critical handoff rules

1. **Planner does NOT implement.** Planner produces the plan, drops it in
   `planned/`, posts to Jira, stops. The Dev Agent picks up later from the
   user's "start dev" command.
2. **Dev does NOT re-plan.** The plan file in `planned/` is the spec. If
   something in the plan is wrong, surface it and ask — don't silently rework.
3. **One file, one status, one folder.** The `status:` frontmatter field
   must match the folder. If a file is in the wrong folder for its status,
   that's a workflow error — stop and reconcile.
4. **Sprint check is mandatory.** The Planner Agent must verify the story
   is in the active sprint. If not, it asks the user before proceeding.
   (This is the one place planning is NOT autonomous.)
5. **Dev Agent does NOT merge or transition to Done.** Pushing to the
   feature branch + opening the PR is the end of the Dev workflow. Merging
   to `main` and transitioning Jira to `Done` are user-only actions.

See `<workspace>/Planning/README.md` for the full protocol and
`<workspace>/Planning/_template/story-plan-template.md` for the plan format.
