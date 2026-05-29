# Triage Agent — `agents/orchestrator/triage.py`

> Jira: **CM-30** | Epic: CM-5 (Agent 1 — Enhanced Triage Agent) | Phase 1

Triage is the first specialist agent and the single entry point of the
LangGraph spine. It replaces the CM-28 keyword **stub** with real GPT-4o-mini
classification that emits a Pydantic-validated `intent` / `urgency` / `tone`,
looks up the tenant's recent ticket history, and routes the message to the
correct downstream agent — all without changing the CM-28 graph topology.

---

## 1. What it does, in order

The `triage` node body in `agents/orchestrator/nodes.py`:

1. **Guardrail check first** — `guardrails.check(state)` (CM-26/28 Stop Rules).
   If a rule has tripped, route straight to `guardrail_terminated`; the
   classifier is never called.
2. **History lookup** — `get_history_provider().recent_tickets(tenant_id)`
   (AC #5). Today this is a no-op stub returning `[]`; CM-31 swaps in a
   Cosmos-backed provider.
3. **Classify** — `get_triage_classifier().classify(message, history)`. The
   message is the CM-29 PII-masked `normalized.content` when a channel adapter
   has run, else the raw `raw_message`.
4. **Persist + route** — write `intent` / `urgency` / `tone` / `history` to
   `AgentState`, bump `cost_so_far` by the classifier's per-call estimate, and
   set `routes=[route_for(classification)]`.

---

## 2. Classification contract (AC #1–#4)

`TriageClassification` (Pydantic) is the structured output the LLM is **forced**
to emit via `ChatOpenAI(...).with_structured_output(...)`. Its three
classification fields reuse the CM-28 `StrEnum`s, so the schema can never drift
from `AgentState`:

| Field        | Type / values |
|--------------|---------------|
| `intent`     | `maintenance` / `inquiry` / `escalation` / `follow-up` |
| `urgency`    | `emergency` / `high` / `medium` / `low` |
| `tone`       | `neutral` / `frustrated` / `angry` / `urgent` |
| `rationale`  | one-line audit string (default `""`) |
| `confidence` | `0.0–1.0` (default `1.0`) |

A response that doesn't validate (e.g. the model invents `"complaint"`) raises
at the model boundary rather than silently mis-routing.

---

## 3. Routing matrix (AC #6)

`route_for(classification)` maps **intent → downstream node**. The returned
strings match the conditional-edge keys in `graph.py` exactly.

| Intent        | Route         | Why |
|---------------|---------------|-----|
| `maintenance` | `maintenance` | ticket lifecycle (CM-31) |
| `inquiry`     | `knowledge`   | policy / FAQ answers |
| `escalation`  | `escalation`  | empathetic agent + HITL (CM-32) |
| `follow-up`   | `maintenance` | status of an existing ticket |
| `unknown`     | `knowledge`   | safe default — can ask a clarifying question |

v1 routes **purely by intent**. `urgency` and `tone` are persisted on
`AgentState` for downstream prioritisation; a tone/urgency → escalation
override is deferred to CM-32 to avoid premature cross-agent coupling.

---

## 4. LLM vs. heuristic — the no-credentials contract

`get_triage_classifier()` is an env-driven selector, mirroring CM-28's
`get_checkpointer()`:

```python
def get_triage_classifier() -> TriageClassifier:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key and key != "REPLACE-ME":
        return LLMTriageClassifier()   # GPT-4o-mini, structured output
    return HeuristicTriageClassifier() # deterministic keyword fallback
```

- **`LLMTriageClassifier`** — real GPT-4o-mini (AC #8). Lazy-imports
  `langchain_openai` so the import cost / key requirement only lands when it's
  actually used.
- **`HeuristicTriageClassifier`** — deterministic keyword classifier. Preserves
  CM-28's exact stub routing (`leak`/`fix`/`broken` → maintenance,
  `human`/`escalat` → escalation, else → inquiry/knowledge) so the
  hello-world suite and the credential-free `python -m agents.orchestrator.demo`
  keep working. It will **not** hit the AC #7 >90% bar — that target is the
  LLM's, asserted by the operator eval CLI.

> **OpenAI vs. Azure OpenAI.** We use plain `ChatOpenAI` + `OPENAI_API_KEY`,
> matching the CM-23 `langchain_demo` precedent. The Key Vault secret
> `azure-openai-key` is reserved for the planned Azure OpenAI resource; once it
> is provisioned, swap `ChatOpenAI` for `AzureChatOpenAI` inside
> `LLMTriageClassifier` — a one-class change behind the selector.

---

## 5. Eval (AC #7)

- **Dataset:** `tests/eval/triage_seed.jsonl` — 200 labelled condo messages,
  balanced across every intent / urgency / tone. Same shape CM-23 seeds to
  LangSmith (`{"inputs": {...}, "outputs": {...}}`); upload with
  `infra/scripts/seed-langsmith-dataset.py`.
- **Scorer:** `agents/orchestrator/eval.py` — pure functions
  (`score_classification`, `accuracy`, `run_eval`). **Intent** accuracy is the
  gate (`> 90%`); `urgency` / `tone` are reported as secondary diagnostics
  (they're fuzzier, so gating them would make the eval flaky).
- **Offline test:** `tests/eval/test_triage_eval.py` — proves the dataset is
  ≥200, valid, balanced, and the scorer math is correct. Runs with no key.
- **Live gate (operator):**

  ```bash
  export OPENAI_API_KEY=<key>
  python infra/scripts/eval-triage.py            # all 200, gates on >90% intent
  python infra/scripts/eval-triage.py --limit 20 --show-mismatches   # cheap smoke
  ```
  Exits non-zero if intent accuracy doesn't clear the gate.

---

## 6. Extension seams (future stories)

| Seam | Today | Future |
|------|-------|--------|
| `get_history_provider()` | `NoopTicketHistory` → `[]` | CM-31 Cosmos `tickets` provider |
| `get_triage_classifier()` | OpenAI / heuristic | Azure OpenAI when provisioned |
| tone/urgency override | not applied (intent-only routing) | CM-32 escalation override |
