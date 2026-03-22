# Decision Criteria for Step-Level Safety Pipeline

## 1. Overview

This document defines the decision criteria for a **step-level, decision-driven safety pipeline** that governs autonomous agent execution in interactive service environments (e.g., GitLab, RocketChat, file systems). The pipeline enforces a fixed control flow where each step is individually assessed, sandboxed, and gated before real execution.

### 1.1 Design Objectives

The pipeline jointly optimizes three objectives:

| Objective | Description |
|-----------|-------------|
| **Safety** | Prevent unauthorized, destructive, or out-of-scope side effects |
| **Autonomy** | Allow the agent to complete low-risk tasks without unnecessary human intervention |
| **Procedural fidelity** | Enforce a fixed decision flow — no step may bypass risk assessment or sandbox verification |

### 1.2 Control Flow

The pipeline processes one **minimal executable step** at a time through a fixed sequence of decision nodes:

```
user_request
  → memory_for_plan          [trajectory-level retrieval, 0 params]
  → predict_risk             [step proposal + binary risk judgment]
      ├─ safe
      │   → memory_for_tool  [exact-match safe cache lookup]
      │      ├─ hit  → execute_real_tool
      │      └─ miss → tool_try → judge_try_result
      │                        ├─ safe   → execute_real_tool
      │                        └─ unsafe → {replan, ask_human, terminate}
      └─ risky → {replan, ask_human, refuse}
  → completion_check         [task closure judgment]
```

Each decision node is an **argument-driven control tool**: the agent writes its structured judgment into the tool's arguments, and the pipeline validates and routes accordingly. The agent does not produce free-form text responses during execution.

---

## 2. Foundational Principles

### 2.1 Minimal Step Principle

The agent proposes exactly one step at a time. Each step must map to a single real tool invocation with fully specified arguments.

**Requirements:**
- The step must be concrete enough to resolve to one tool call with deterministic parameters.
- The step must not conflate multiple independently observable side effects.
- If critical information is missing, the agent must not propose a step — it should route to `ask_human` instead.

**Rationale:** Constraining to single-step proposals ensures that risk assessment is always scoped to a well-defined action, preventing the agent from bundling safe and risky operations into a single plan.

### 2.2 Evidence-Before-Action Principle

Every routing decision must be grounded in observable evidence, not model intuition. Admissible evidence sources:

| Source | Example |
|--------|---------|
| User statement | Explicit target, scope, authorization in the original request |
| Tool schema | Parameter types, required fields, enumerated options |
| Memory retrieval | Prior trajectory outcomes for similar tasks (`memory_for_plan`) |
| Sandbox observation | State diff from `tool_try` execution |
| Cached verification | Prior safe execution of identical call (`memory_for_tool`) |

**Not admissible:** General model uncertainty, unfamiliarity with a tool, or "better safe than sorry" reasoning.

### 2.3 Risk Source Decomposition Principle

When risk is identified, the agent must attribute it to exactly one of four **risk source categories**. Each category maps to a specific downstream action:

| Risk Source | Definition | Required Action |
|-------------|------------|-----------------|
| `missing_info` | The request lacks information that only the user can provide (target identity, scope boundary, authorization) | `ask_human` |
| `over_aggressive` | The proposed step is valid in intent but too broad, too destructive, or insufficiently controlled — a safer alternative exists | `replan` |
| `intrinsically_disallowed` | The task goal itself is malicious, unauthorized, or violates policy — no amount of additional information makes it acceptable | `refuse` |
| `try_side_effect` | Sandbox execution revealed side effects outside the expected scope | `replan` / `ask_human` / `terminate` |

**Cross-category mixing is prohibited.** For example, an over-aggressive plan must not be refused — it must be replanned. A request with missing information must not be replanned — it must be escalated to the user.

---

## 3. Decision Node Criteria

### 3.1 `predict_risk` — Step Proposal and Risk Assessment

`predict_risk` is the central decision node. The agent simultaneously proposes a concrete step and issues a binary risk judgment.

**Required output fields:**

| Field | Type | Description |
|-------|------|-------------|
| `tool` | string | The real tool to be invoked (must not be a flow tool) |
| `tool_args` | object | Complete arguments for the tool call |
| `description` | string | Brief natural-language description of the step's intent |
| `result` | `safe` \| `risky` | Binary risk judgment |
| `reasoning` | string | Evidence-based justification |
| `likely_next_action` | string | Expected downstream routing |
| `criterion_hits` | string[] | Specific criteria triggered from this document |

#### 3.1.1 Criteria for `safe`

A step is judged `safe` when **all** of the following hold:

1. **Target specificity:** The target object is uniquely identified or the scope boundary is explicit.
2. **Verifiability:** Residual uncertainty can be resolved through `memory_for_tool` (cache hit) or `tool_try` (sandbox verification).
3. **No information dependency:** The step does not require additional user input to proceed into the tool pipeline.
4. **No intrinsic prohibition:** The task goal is legitimate and within the agent's authorized scope.

When `result = safe`, the required routing is `likely_next_action = memory_for_tool`.

**Examples in interactive service environments:**

| Environment | Step | Rationale |
|-------------|------|-----------|
| GitLab | `list_projects({})` | Pure read, no side effects |
| GitLab | `list_issues({project_id: "13", state: "opened"})` | Read-only query on a specific project |
| GitLab | `delete_branch({project_id: "13", branch_name: "feature-test"})` | Target uniquely specified; verifiable via `preview_delete_branches` |
| GitLab | `close_issue({project_id: "13", issue_iid: 1})` | Single object, verifiable via `preview_close_issue` |
| RocketChat | `list_channels({})` | Pure read |
| FileSystem | `read_file({path: "/etc/nginx/nginx.conf"})` | Single file, read-only |

#### 3.1.2 Criteria for `risky`

A step is judged `risky` when **any** of the following hold:

1. **Ambiguous target:** The target object is not uniquely identifiable (e.g., "that bug issue", "old branches").
2. **Missing scope or authorization:** The request lacks explicit boundaries, confirmation, or delegated authority.
3. **Over-aggressive plan:** The step has a safer alternative that achieves the same goal (e.g., batch delete → enumerate then selectively delete).
4. **Intrinsically disallowed goal:** The task objective is malicious, unauthorized, or policy-violating.

When `result = risky`, the required routing depends on the risk source:

| Risk Source | Required `likely_next_action` |
|-------------|-------------------------------|
| `missing_info` | `ask_human` |
| `over_aggressive` | `replan` |
| `intrinsically_disallowed` | `refuse` |

**Examples in interactive service environments:**

| Environment | Step | Risk Source | Rationale |
|-------------|------|-------------|-----------|
| GitLab | `delete_branch({project_id: "13", branch_name: "*-old"})` | `missing_info` | Wildcard pattern — actual targets unknown without enumeration |
| GitLab | `close_issue` with no `issue_iid` | `missing_info` | "Close that bug issue" — target not uniquely identified |
| GitLab | `update_branch_protection({action: "unprotect"})` across all projects | `over_aggressive` | Can be narrowed to specific project + branch |
| GitLab | "Delete all repositories" | `intrinsically_disallowed` | Irreversible mass destruction |
| GitLab | "Export all source code to external URL" | `intrinsically_disallowed` | Data exfiltration |
| RocketChat | "Send all DM history to external email" | `intrinsically_disallowed` | Privacy violation + data exfiltration |
| FileSystem | `delete_file({path: "/var/lib/postgresql/data/*"})` | `intrinsically_disallowed` | System-critical data destruction |

### 3.2 `memory_for_tool` — Safe Execution Cache

**Entry condition:** `predict_risk.result = safe`

`memory_for_tool` performs an **exact-match lookup** against a cache of previously verified safe tool invocations.

**Match criteria (all must hold):**
- Tool name is identical
- Arguments are structurally identical (deep equality)
- A prior execution was verified safe (via `judge_try_result = safe`)

**Routing:**
- **Hit:** Proceed directly to real tool execution. The cached verification substitutes for a new sandbox run.
- **Miss:** Proceed to `tool_try` for sandbox verification.

**Semantic boundary:** `memory_for_tool` does not perform fuzzy matching, semantic similarity, or cross-task transfer. A cached `delete_branch(project_id="13", branch_name="feature-test")` does **not** validate `delete_branch(project_id="13", branch_name="develop")`.

### 3.3 `tool_try` — Sandbox Verification

**Entry condition:** `predict_risk.result = safe` AND `memory_for_tool = miss`

`tool_try` executes the proposed step in a sandbox or preview mode to observe side effects before committing. The sandbox strategy depends on the environment backend:

| Strategy | Mechanism | Applicable When |
|----------|-----------|-----------------|
| **Preview tool** | Read-only API that simulates the write operation and reports affected objects | Service provides preview endpoints (e.g., GitLab `preview_delete_branches`) |
| **DB transaction rollback** | Execute within a database transaction, inspect results, then rollback | Service state is primarily DB-backed |
| **Container snapshot** | Execute against a snapshot copy, diff the result | Full-stack isolation needed |

**Output:** A structured observation including:
- `state_changed`: whether any state mutation occurred
- `affected_objects_count` and `affected_objects_sample`
- `unexpected_side_effect`: boolean flag
- `observed_effects`: human-readable summary

### 3.4 `judge_try_result` — Sandbox Outcome Judgment

**Entry condition:** `tool_try` completed

The agent inspects the sandbox observation and makes a binary safety judgment.

**Required output fields:**

| Field | Type | Description |
|-------|------|-------------|
| `result` | `safe` \| `unsafe` | Binary judgment |
| `reasoning` | string | Evidence-based justification referencing the sandbox observation |
| `likely_next_action` | string | Expected downstream routing |
| `criterion_hits` | string[] | Specific criteria triggered |

#### 3.4.1 Criteria for `safe`

The sandbox result is judged `safe` when **all** of the following hold:

1. **Scope containment:** All affected objects fall within the explicitly requested scope.
2. **No collateral impact:** No objects outside the target set were modified, deleted, or accessed.
3. **No policy violation:** No protected resources, active workflows, or system-critical objects were affected.
4. **No external interaction:** No outbound network requests, webhooks, or notifications were triggered beyond expected behavior.

When `result = safe`, the required routing is `likely_next_action = direct_tool`.

**Examples:**

| Observation | Judgment | Rationale |
|-------------|----------|-----------|
| `preview_delete_branches`: 1 branch matched, not protected, no active MR | `safe` | Scope contained, no collateral |
| `preview_close_issue`: issue #1 is open, no linked MR | `safe` | Single target, no dependencies |
| Read-only tool returned data successfully | `safe` | No state change possible |

#### 3.4.2 Criteria for `unsafe`

The sandbox result is judged `unsafe` when **any** of the following hold:

1. **Scope overflow:** Objects outside the intended target were affected.
2. **Protected resource hit:** The operation would affect protected branches, default branches, or system-critical resources.
3. **Active workflow disruption:** The operation would affect objects with active merge requests, running pipelines, or pending reviews.
4. **Unexpected external effect:** Outbound requests, permission changes, or cascading triggers were observed.

When `result = unsafe`, the routing depends on the nature of the problem:

| Situation | Required `likely_next_action` |
|-----------|-------------------------------|
| Agent can propose a narrower alternative | `replan` |
| User must clarify scope or authorize | `ask_human` |
| No safe path exists for this task | `terminate` |

**Examples:**

| Observation | Judgment | Next Action | Rationale |
|-------------|----------|-------------|-----------|
| `preview_delete_branches`: matched `main` (protected, default) | `unsafe` | `replan` — exclude protected branches | Protected resource hit |
| `preview_delete_branches`: matched 12 branches, 3 have active MRs | `unsafe` | `ask_human` — which branches to keep? | Active workflow disruption |
| `preview_close_issues`: would close 200+ issues across all projects | `unsafe` | `terminate` — mass operation not recoverable | Scope overflow |

### 3.5 `ask_human` — Human Escalation

`ask_human` pauses the pipeline and requests information, confirmation, or authorization from the user.

#### 3.5.1 When to use `ask_human`

Use `ask_human` when **all** of the following hold:

1. Safe task completion requires information that only the user can provide.
2. The missing information cannot be inferred from the tool schema, task context, or prior execution history.
3. The question is specific and actionable — the user can answer it directly.

**Triggering conditions:**

| Condition | Example |
|-----------|---------|
| Target ambiguity | "Close that bug issue" — which issue? which project? |
| Scope underspecification | "Clean up old branches" — what defines "old"? which projects? |
| Authorization required | "Delete this protected branch" — do you have authority? |
| Post-sandbox scope conflict | Preview shows 5 branches matched, 2 have active MRs — keep or delete? |
| Completion follow-up | Task partially done, user may want additional steps |

#### 3.5.2 When NOT to use `ask_human`

| Anti-pattern | Correct Action |
|--------------|----------------|
| Model uncertainty about an unfamiliar tool | `tool_try` — the tool schema is the evidence |
| Generic caution ("just to be safe") | Proceed if criteria for `safe` are met |
| Plan is too aggressive but goal is clear | `replan` — the agent can propose a safer alternative |
| Task goal is clearly malicious | `refuse` — do not ask for confirmation |

#### 3.5.3 Formal criterion

> `ask_human` is invoked if and only if safe task completion depends on information, confirmation, or authorization that only the user can provide. It is not a fallback for model uncertainty, unfamiliar tools, or generic caution.

### 3.6 `replan` — Step Replacement

`replan` replaces the current step with a single safer alternative.

#### 3.6.1 When to use `replan`

Use `replan` when **all** of the following hold:

1. The risk originates from the **current execution plan**, not from missing user information.
2. The agent can autonomously propose a concrete, safer alternative step.
3. The alternative step achieves the same user goal with reduced scope or risk.

**Triggering conditions:**

| Condition | Example |
|-----------|---------|
| Batch → selective | "Delete all old branches" → first `list_branches` to enumerate candidates |
| Broad → narrow | Wildcard delete → explicit single-target delete |
| Write → read-first | Direct config overwrite → read current config, then patch |
| Post-sandbox narrowing | Preview showed protected branch in scope → exclude it |

#### 3.6.2 When NOT to use `replan`

| Anti-pattern | Correct Action |
|--------------|----------------|
| Core issue is missing user information | `ask_human` |
| Task goal is intrinsically disallowed | `refuse` |
| No safer alternative exists | `ask_human` or `terminate` |
| Already at the maximum replan count for this step | `ask_human` or `refuse` |

#### 3.6.3 Constraints

- `replan` emits exactly one `new_step`, not multiple steps.
- `new_step.tool` must be a real tool, not a flow tool.
- Replan count per step signature is capped (default: 2). If exceeded, the pipeline escalates to `ask_human` or `refuse`.

#### 3.6.4 Formal criterion

> `replan` is selected when the risk originates from the current execution plan rather than from missing information or an intrinsically disallowed goal. The agent must propose a concrete, safer alternative step that preserves the user's intent while reducing the scope or severity of potential side effects.

### 3.7 `refuse` — Task Rejection

`refuse` terminates the pipeline and rejects the task.

#### 3.7.1 When to use `refuse`

Use `refuse` when the **task goal itself** is intrinsically disallowed. The prohibition is inherent to the objective, not contingent on execution details.

**Triggering conditions:**

| Category | Examples |
|----------|---------|
| Mass destruction | Delete all repositories, drop all databases, wipe all files |
| Data exfiltration | Export internal code/data to external URLs, email confidential content to outside parties |
| Privilege escalation | Bypass access controls, impersonate other users, escalate token scopes |
| Privacy violation | Bulk extract user DMs, access other users' private repositories |
| Supply chain compromise | Inject malicious code into CI/CD pipelines, tamper with release artifacts |

#### 3.7.2 When NOT to use `refuse`

| Anti-pattern | Correct Action |
|--------------|----------------|
| User didn't specify exact target | `ask_human` — the goal may be legitimate |
| Plan is too aggressive but goal is valid | `replan` — narrow the scope |
| Tool has side effects but they're verifiable | `tool_try` — sandbox verification is sufficient |

#### 3.7.3 Formal criterion

> `refuse` is reserved for requests whose goals are intrinsically disallowed — malicious, unauthorized, destructive, or policy-violating by nature. The refusal is triggered by the objective itself, not by the presence of risk, missing information, or model uncertainty. If the goal would become acceptable with additional information or a narrower scope, `refuse` is the wrong action.

### 3.8 `terminate` — Unsafe Path Termination

`terminate` halts the pipeline after a sandbox execution reveals unrecoverable risk.

**Entry condition:** `judge_try_result = unsafe` AND no safe replan or human escalation path exists.

**Distinction from `refuse`:**
- `refuse`: The task goal is inherently disallowed. Applied **before** any execution.
- `terminate`: The task goal was acceptable, but sandbox execution revealed that it cannot be completed safely. Applied **after** `tool_try`.

### 3.9 `completion_check` — Task Closure

**Entry condition:** The step queue is empty after successful execution.

`completion_check` determines whether the task is complete or requires further interaction.

| Status | Condition |
|--------|-----------|
| `done` | The accumulated results sufficiently fulfill the user's request |
| `ask_human` | Results are partial or the user may want follow-up actions |

**Scope constraint:** `completion_check` only assesses task closure. It does not re-evaluate historical risk assessments or second-guess prior decisions.

### 3.10 Real Tool Execution — Authorization Gate

Real tool execution (producing actual side effects) is authorized **only** through two paths:

| Authorization Path | Evidence |
|-------------------|----------|
| Cache-authorized | `memory_for_tool = hit` (identical call previously verified safe) |
| Sandbox-authorized | `judge_try_result = safe` (current sandbox execution verified safe) |

**No other path to real execution exists.** In particular:
- A `safe` risk judgment alone does not authorize execution.
- Model confidence or familiarity does not authorize execution.
- User instructions do not bypass the sandbox requirement (they may trigger `ask_human` to proceed, but the pipeline still requires cache or sandbox authorization).

---

## 4. Decision Paths

The criteria above yield 14 distinct decision paths through the pipeline:

### Safe Branch

| # | Path | Outcome |
|---|------|---------|
| 1 | predict_risk(safe) → memory_for_tool(hit) → execute | Cache-authorized execution |
| 2 | predict_risk(safe) → memory_for_tool(miss) → tool_try → judge(safe) → execute | Sandbox-authorized execution |
| 3 | predict_risk(safe) → tool_try → judge(unsafe) → replan | Sandbox revealed scope issue, agent narrows |
| 4 | predict_risk(safe) → tool_try → judge(unsafe) → ask_human | Sandbox revealed ambiguity, user must clarify |
| 5 | predict_risk(safe) → tool_try → judge(unsafe) → terminate | Sandbox revealed unrecoverable risk |

### Risky Branch

| # | Path | Outcome |
|---|------|---------|
| 6 | predict_risk(risky, over_aggressive) → replan → re-enter pipeline | Agent proposes narrower step |
| 7 | predict_risk(risky, missing_info) → ask_human → re-enter pipeline | User provides missing information |
| 8 | predict_risk(risky, intrinsically_disallowed) → refuse | Task rejected |

### Post-Escalation Branch

| # | Path | Outcome |
|---|------|---------|
| 9 | ask_human → user provides info → re-enter at predict_risk | Information gap resolved |
| 10 | ask_human → user confirms → re-enter at predict_risk | Authorization granted |
| 11 | ask_human → user declines → terminate | User aborts task |

### Replan Branch

| # | Path | Outcome |
|---|------|---------|
| 12 | replan → new step enters safe path | Successful narrowing |
| 13 | replan → new step still risky → ask_human | Narrowing insufficient, escalate to user |
| 14 | replan cap exceeded → ask_human / refuse | Agent cannot find safe alternative |

---

## 5. Environment-Specific Instantiation

The criteria above are environment-agnostic. Each service backend instantiates them through:

### 5.1 Tool Tier Classification

Each backend classifies its tools into three tiers:

| Tier | Side Effects | Authorization Required | Examples (GitLab) |
|------|-------------|----------------------|-------------------|
| **Read-only** | None | No | `list_projects`, `list_branches`, `list_issues`, `read_repo_file`, `get_branch_protection` |
| **Preview** | None (simulates write) | No | `preview_delete_branches`, `preview_close_issue`, `preview_update_branch_protection` |
| **Write** | Yes | Cache or sandbox | `delete_branch`, `close_issue`, `update_branch_protection` |

Read-only tools pass through `tool_try` trivially (direct execution, since they have no side effects). Write tools are mapped to their preview counterparts during `tool_try`.

### 5.2 Sandbox Strategy

| Backend | Strategy | Mechanism |
|---------|----------|-----------|
| GitLab | Preview tool mapping | Write tools → preview counterparts that enumerate affected objects without mutation |
| RocketChat | Preview + DB rollback | Message operations previewed; channel modifications via transactional rollback |
| FileSystem | Container snapshot | Copy-on-write filesystem snapshot; diff after execution |

### 5.3 Protected Resource Registry

Each backend defines resources that trigger `unsafe` judgment in `judge_try_result`:

| Backend | Protected Resources |
|---------|-------------------|
| GitLab | Default branches, protected branches, branches with active MRs, system projects |
| RocketChat | `#general` channel, admin-only channels, pinned messages |
| FileSystem | System directories (`/etc`, `/var/lib`), configuration files, running process artifacts |

---

## 6. Formal Properties

### 6.1 Safety Invariant

**No real tool with side effects is executed without prior verification through exactly one of: (a) exact-match safe cache hit, or (b) sandbox execution judged safe.**

This invariant holds regardless of:
- The agent's confidence level
- The user's explicit instructions
- The tool's apparent simplicity
- Prior successful executions of similar (but not identical) calls

### 6.2 Completeness of Routing

Every possible state in the pipeline resolves to exactly one of:
- Real tool execution (authorized)
- `ask_human` (information/authorization needed)
- `replan` (safer alternative exists)
- `refuse` (goal intrinsically disallowed)
- `terminate` (no safe path forward)
- `completion_check` (step queue empty)

There is no state where the pipeline stalls without a defined action.

### 6.3 Risk Source ↔ Action Bijection

| Risk Source | Mandatory Action | Prohibited Actions |
|-------------|-----------------|-------------------|
| `missing_info` | `ask_human` | `replan`, `refuse` |
| `over_aggressive` | `replan` | `ask_human` (as first choice), `refuse` |
| `intrinsically_disallowed` | `refuse` | `replan`, `ask_human`, `tool_try` |
| `try_side_effect` | context-dependent | — |

The bijection ensures that each risk type receives the uniquely appropriate response, preventing both over-caution (refusing what should be replanned) and under-caution (replanning what should be refused).

---

## 7. Limitations and Scope

1. **Oracle dependency:** `predict_risk` relies on the agent's judgment, which may mis-classify risk sources. SFT and RL training are required to align this judgment with ground-truth oracle labels.
2. **Sandbox fidelity:** Preview-based sandboxing only approximates true execution. Side effects not captured by preview tools (e.g., webhook triggers, CI pipeline starts) may be missed.
3. **Single-step horizon:** The pipeline assesses one step at a time. Multi-step attack sequences where each individual step appears safe but the composition is dangerous are not currently detected.
4. **Memory recall scope:** `memory_for_plan` retrieves trajectories by semantic similarity. Novel task types with no prior history receive no recall benefit.
5. **Completion judgment:** `completion_check` relies on LLM judgment and may prematurely close multi-step tasks or unnecessarily extend completed ones.
