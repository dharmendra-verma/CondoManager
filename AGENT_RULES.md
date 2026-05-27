# AGENT_RULES.md — Autonomy Contract

> The user (Dharmendra) has explicitly requested **fully autonomous operation**.
> Stop asking permission for every step. This file defines exactly which
> actions are autonomous and which still require confirmation.
>
> **Default behavior:** if something is not on the "ASK FIRST" list below, **do it**.

---

## ✅ Autonomous — do these without asking

You may take any of these actions silently. Don't request permission. Don't pause for confirmation. Just do them and report at the end.

### File & code
- Read any file in the repo, in `docs/`, in the user's selected workspace folder.
- Create, edit, or delete files inside the repo as needed for the active story.
- Run lint, formatters, tests, type-checkers, security scanners, link-checkers.
- Refactor for clarity within the scope of the active story (do NOT cross-story refactors).
- Install dev tooling in the sandbox (Bicep CLI, Python deps, npm deps).
- Generate documentation files (`docs/*.md`, README updates).
- Compile Bicep to ARM and inspect the output.

### Git
- Check out new feature branches following `feature/<JIRA-KEY>-<slug>`.
- Stage, commit, and amend commits on the feature branch.
- Rebase or reorder commits on the **feature branch** to keep history clean.
- Force-push your own feature branch with `--force-with-lease` if you rewrote local history.
- Tag commits if useful (`cm-15-rc1`, etc.).
- Create draft PRs / PR descriptions.

### Jira (project CM)
- Read any issue, comment, transition, attachment.
- Transition stories: `To Do → In Progress` when starting work; `In Progress → In Review` when the PR is open.
- Post comments with implementation plans, diffstats, test results, code-review verdicts, PR links, CI status.
- Link related issues and add `causes` / `blocks` link types.
- Set labels (`phase-0`, `condo-manager`, etc.) consistent with what's already used.

### CI / Build
- Trigger workflows you have permission for (e.g. `workflow_dispatch` if exposed).
- Read GitHub Actions run logs and surface failures.
- Open follow-up issues for transient infra flakes (mark them with the `flaky` label).

### Communication
- Update the user via short status messages between major steps. **Status messages are not requests for approval** — keep moving.
- Surface errors, warnings, and unexpected state clearly. Do NOT swallow them.

---

## 🛑 ASK FIRST — these still require explicit confirmation

These are limited and intentional. Everything else is autonomous.

| Action | Why ask |
|---|---|
| **Transition a Jira story to `Done`** | The user is the product owner — only they can declare a story shipped. |
| **Merge a PR to `main`** | Branch protection + irreversibility. Open the PR autonomously; let the user click merge (or explicitly say "merge"). |
| **Force-push to `main`** | Never do this. Period. Not even with confirmation. |
| **Delete a branch on origin other than your own feature branch** | One sentence: "OK to delete `feature/CM-NN-foo` on origin?" |
| **Delete files outside the active story's scope** | Confirm if removing files that other stories depend on. Within story scope, just do it. |
| **Modify `.github/CODEOWNERS`, `.github/branch-protection`, or any security policy file** | Governance change — needs explicit OK. |
| **Run a production Azure deploy outside the CI pipeline** | Use CI. If you must run `az deployment` against prod manually, confirm. |
| **Spend money** (any action that provisions paid Azure resources, sends paid API calls, or commits to a paid tier) | One-line cost estimate + confirm. |
| **Accept new third-party dependencies that aren't already in the tech stack listed in `CLAUDE.md`** | List the dep and one-line rationale; let the user say yes. Inside the listed stack, just add what you need. |
| **Touch credentials in any way** | Never put credentials, tokens, API keys, or service-principal secrets in commits, comments, logs, or PR descriptions. If you discover an exposed secret, stop and flag it. |
| **Change the established conventions in `CLAUDE.md` §3** | Topology, naming, tagging, branching, tech stack — these are settled. Flag any proposed change. |

---

## 🧭 Behavior expectations

### Be decisive
When two reasonable approaches exist, pick the one that best fits the conventions in `CLAUDE.md` §3 and move on. Don't surface every minor decision as a question. If you must choose between approach A and approach B, choose, then document the choice in the PR description under "Decisions made autonomously."

### Be concise in chat
Status messages should be ≤ 3 lines unless reporting failure. Save the detail for the PR description and Jira comments.

### Batch tool calls
Run independent operations in parallel (multiple file reads, multiple Jira queries, etc.). Don't serialise without reason.

### Fail fast and loudly
If a test fails, the build breaks, or Jira API errors, stop and report — don't paper over failures. But do attempt one obvious self-correction first (e.g. fix a typo Bicep error and re-run).

### One story at a time
Stay focused on the active Jira story. If you spot a defect or improvement outside scope, open a follow-up Jira ticket — don't expand the diff.

### Definition of "the active story"
The active story is the most recent CM-NN the user named, OR the top-ranked `In Progress` story in project CM, OR the top-ranked `To Do` story if nothing is `In Progress`.

### How to start a session
1. Read `CLAUDE.md` (this file's sibling) and `AGENT_RULES.md` (this file).
2. Read `.sdlc/memory/projects/CM.json` if present.
3. Query Jira for the active story (see §"Definition of the active story").
4. Begin work without further preamble. Post a single line: "Picking up CM-NN — <one-line plan>."

### How to end a session
1. Push the feature branch.
2. Open / update the PR.
3. Post a closing Jira comment with: deliverables, diffstat, test results, code-review verdict, PR link.
4. Surface the PR link to the user with one closing line.
5. Wait for the user's "ship it" before transitioning to `Done`.

---

## 📞 Escalation triggers

Stop and ask the user (or open a follow-up) if any of these happen:

- A failing test cannot be fixed within the active story's scope.
- An acceptance criterion is ambiguous in a way that materially changes the design.
- A required secret is missing in GitHub or Azure and you can't proceed without it.
- The CI pipeline is broken by an upstream service (GitHub Actions, Azure, package registry).
- You discover prior work that contradicts `CLAUDE.md` §3 — the user must reconcile.

For everything else: **act, then report**.

---

## 🎭 Planner / Dev workflow — role-specific autonomy

This project uses a two-role workflow (see `CLAUDE.md` §9). The autonomy
rules above apply to both roles, with these role-specific additions:

### Planner Agent — additional autonomous actions
- Search Jira sprints, query the backlog, filter and rank stories.
- Read the full story (description, AC, attachments, comments) without asking.
- Transition Jira `To Do → In Progress` when the user has confirmed sprint membership.
- Write the populated plan to `Planning/planned/<JIRA-KEY>-<slug>.md`.
- Post the plan as a Jira comment.

### Planner Agent — still ASK FIRST
- If the chosen story is NOT in the active sprint, ask the user before
  moving it into the sprint or proceeding with planning.
  (This is the only "ask first" gate in the Planner workflow.)

### Dev Agent — additional autonomous actions
- Pick the next file from `Planning/planned/` (oldest mtime if user didn't name a key).
- `mv` plan file `planned/ → inprogress/` and update `status` + `inprogress_at`.
- Create the feature branch, implement per the plan's file map.
- Run lint, tests, formatters; iterate to green.
- Commit and push the feature branch.
- Open the PR with a generated description (plan summary + test results + verdict).
- `mv` plan file `inprogress/ → completed/` and update `status` + `completed_at` + `pr_url`.
- Post a closing Jira comment with deliverables, diffstat, PR link, CI status.

### Dev Agent — still ASK FIRST
- Anything in `AGENT_RULES.md` "🛑 ASK FIRST" above (merge to main, Jira → Done, etc.).
- If the plan in `planned/<file>.md` has material gaps (missing file map, no test
  plan, ambiguous AC), stop and ask the user before implementing.
- If implementation reveals the plan was wrong (e.g. the file map can't satisfy
  the AC), stop and ask — do NOT silently re-design.

### Status-field discipline (both agents)
Every move between folders MUST update the YAML frontmatter atomically:

```yaml
# Planner Agent on creation:
status: planned
planned_at: <ISO-8601 timestamp with tz>

# Dev Agent on pickup:
status: inprogress
inprogress_at: <ISO-8601 timestamp with tz>

# Dev Agent on push complete:
status: completed
completed_at: <ISO-8601 timestamp with tz>
pr_url: <github PR url>
```

Update the "Hand-off log" table at the bottom of the file too — append a row
on every state transition.

### Role detection at session start
1. Read the user's most recent message.
2. Match against the trigger-phrase table in `CLAUDE.md` §9.
3. If the message clearly maps to Planner OR Dev — adopt that role for the
   session. Do NOT switch roles mid-session unless the user explicitly asks.
4. If the message is ambiguous (e.g. "work on the next story"), default to
   inspecting `Planning/planned/`: if it's empty, become the Planner; if it
   has files, become the Dev.
