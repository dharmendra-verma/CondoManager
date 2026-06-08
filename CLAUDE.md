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

## 9. Planner / Dev two-agent workflow — **fully autonomous, hands-free**

When the user says any of the trigger phrases below, the agent does
**every step**: git, folder ops, slug derivation, branch naming, PR opening.
The user never types `git worktree add`, `git checkout`, `mv`, or any path.

### Known paths (memorize)

```
WORKSPACE      = C:\Users\to_dh\AppData\Roaming\Claude\Working\Condo Manager
CLONE_DEFAULT  = the dir containing this CLAUDE.md (use `git rev-parse --show-toplevel`)
                 if not in a clone, fall back to common locations and ask if none found
PLANNING       = WORKSPACE\Planning
  PLANNED      = PLANNING\planned         ← Planner writes here
  INPROGRESS   = PLANNING\inprogress      ← Dev moves here on pickup
  COMPLETED    = PLANNING\completed       ← Dev moves here after push
  TEMPLATE     = PLANNING\_template\story-plan-template.md
```

Each plan file `<JIRA-KEY>-<slug>.md` walks through `planned → inprogress → completed`.
Its YAML `status:` field must always match the folder it sits in.

### Trigger phrases → role

| User says... | Role |
|---|---|
| "Plan CM-NN" / "Plan the next story" / "What's next to plan" / "Plan for next story" | **Planner** |
| "Implement CM-NN" / "Start dev" / "Start implementing CM-NN" / "Start implementing the next planned story" / "Pick up the next planned story" | **Dev** |

### Slug derivation (used by both roles to name files and branches)

1. Take the Jira `summary` field
2. Lowercase, replace any non-alphanumeric with `-`
3. Collapse repeated `-`, trim leading/trailing `-`
4. Drop filler words: `the`, `a`, `an`, `and`, `or`, `with`, `for`, `of`, `to`, `in`, `on`, `by`, `set`, `up`, `configure`, `provision`, `using`
5. Keep the first 4 significant words, total slug ≤ 40 chars
6. Example: `"Set up Azure Container Apps environment with Bicep IaC"` → `azure-container-apps-environment`
7. **Branch name** = `feature/<JIRA-KEY>-<slug>` (uppercase key!)
8. **Plan filename** = `<JIRA-KEY>-<slug>.md`

### Planner role — full procedure (autonomous except step 2)

1. **Resolve target story:**
   - If the user named `CM-NN`: fetch it from Jira.
   - Otherwise: query `project = CM AND issuetype = Story AND status = "To Do" AND sprint in openSprints() ORDER BY rank ASC`, pick the top.
2. **Sprint membership gate (only "ask first" step):**
   - If the story is **not** in the active sprint, stop and ask the user.
   - Don't proceed until the user confirms it should be in the sprint.
3. **Transition Jira:** `To Do → In Progress` (transition id `21`).
4. **Derive slug + branch name** per the rules above.
5. **Write the plan file:**
   - Copy `PLANNING\_template\story-plan-template.md` to `PLANNING\planned\<JIRA-KEY>-<slug>.md`
   - Fill in YAML frontmatter: `jira_key`, `status: planned`, `branch`, `planned_at: <ISO-8601 with TZ>`
   - Fully populate every section: story context, implementation plan (with concrete file map), test plan, DoD checklist, hand-off log first row
6. **Mirror to Jira:** post a comment with the plan content so it's visible there too.
7. **Report and STOP.** Don't create a branch. Don't `cd`. Don't run any git commands. Just:
   *"Plan written to `Planning/planned/<JIRA-KEY>-<slug>.md`. Ready for Dev pickup with 'Implement CM-NN' or 'Start dev'."*

### Dev role — full procedure (autonomous, only stops on hard blockers)

A. **Locate the clone:**
   - Try `git rev-parse --show-toplevel` in cwd.
   - If that fails, try common Windows dev paths in order: `C:\Users\to_dh\source\repos\CondoManager`, `C:\Users\to_dh\projects\CondoManager`, `C:\Users\to_dh\code\CondoManager`.
   - If none exist, stop and ask: *"I can't find your local clone — where is it?"*
B. **Sync main:** `git fetch origin && git checkout main && git pull --ff-only origin main`.
C. **Find the plan file:**
   - If user named CM-NN: glob `PLANNING\planned\CM-NN-*.md`, expect exactly one match.
   - Otherwise: pick the oldest by mtime in `PLANNED`.
   - If `PLANNED` is empty AND user didn't name a story: stop, ask user what to implement.
D. **Self-plan if needed:** If the user named CM-NN but no plan file exists for it: run the FULL Planner procedure above first (steps 1-6), then proceed.
E. **Read plan frontmatter:** extract `branch` and `jira_key` fields. Sanity-check `status: planned`.
F. **Create the worktree (auto):**
   ```
   git worktree add ../wt-<JIRA-KEY>-<slug> <branch> main
   ```
   If `wt-<JIRA-KEY>-<slug>` already exists: reuse it (`cd` into it, check out the branch).
G. **`cd` to the worktree** for all subsequent commands.
H. **Move plan file `planned → inprogress`:**
   - `mv PLANNING\planned\CM-NN-<slug>.md PLANNING\inprogress\CM-NN-<slug>.md`
   - Update YAML: `status: inprogress`, `inprogress_at: <ISO-8601>`. Append a row to the Hand-off log table.
I. **Implement** per the plan's file map. Stay in scope; surface scope creep as Jira sub-tasks, don't expand silently.
J. **Test:** run lint/unit/integration per the plan's test plan; iterate to green. Don't proceed if anything fails.
K. **Self code-review** per `AGENT_RULES.md` 7-point checklist. Record verdict in the plan file's "Self code-review verdict" section.
L. **Commit:** Conventional Commits format: `<type>(<area>): CM-NN <one-line summary>`. Multiple commits OK; squash optional.
M. **Push:** `git push -u origin feature/CM-NN-<slug>`.
N. **Open PR:**
   - With `gh`: `gh pr create --base main --head feature/CM-NN-<slug> --title "CM-NN: <summary>" --body "<plan summary + test results + review verdict + 'Closes CM-NN'>"`
   - Without `gh`: print the compare URL.
O. **Move plan file `inprogress → completed`:**
   - `mv PLANNING\inprogress\CM-NN-<slug>.md PLANNING\completed\CM-NN-<slug>.md`
   - Update YAML: `status: completed`, `completed_at`, `pr_url`. Append final row to the Hand-off log.
P. **Post closing Jira comment:** deliverables, diffstat, test results, code-review verdict, PR link.
Q. **Report and STOP:** one line — PR URL + CI status. Do NOT merge to `main`. Do NOT transition Jira to `Done`.

### Hard blockers — when to ask

The agent stops and asks the user ONLY if:
- **Planner step 2:** the story isn't in the active sprint.
- **Dev step A:** can't find the local clone.
- **Dev step D:** plan file has material gaps (missing file map, no test plan, ambiguous AC).
- **Mid-implementation:** the plan turns out to be wrong (don't silently re-design).
- **Push fails for non-trivial reasons** (no creds, branch protection, etc.).

For **everything else** — branch naming, worktree creation, file moves, status updates, Jira transitions to In Progress, PR creation, Jira comments — the agent **acts** and reports after. Never "Can I create the branch?" The answer is yes; just do it.

### Concurrency rule

If the user spawns multiple Dev sessions for different stories, each session creates its own worktree (`wt-<JIRA-KEY>-<slug>`). They share `PLANNING/` (the workspace folder) but have independent git working trees, so they can't conflict on file edits.

See `<WORKSPACE>\Planning\README.md` for the human-readable workflow doc and `<WORKSPACE>\Planning\_template\story-plan-template.md` for the plan format.
