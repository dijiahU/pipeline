# Decision Criteria for Step-Level Safety Pipeline

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
| Gitea | `list_projects({})` | Pure read, no side effects |
| Gitea | `list_issues({project_id: "root/sotopia", state: "opened"})` | Read-only query on a specific project |
| Gitea | `delete_branch({project_id: "root/sotopia", branch_name: "feature-test"})` | Target uniquely specified; verifiable via `preview_delete_branches` |
| Gitea | `close_issue({project_id: "root/sotopia", issue_iid: 1})` | Single object, verifiable via `preview_close_issue` |
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
| Gitea | `delete_branch({project_id: "root/sotopia", branch_name: "*-old"})` | `missing_info` | Wildcard pattern — actual targets unknown without enumeration |
| Gitea | `close_issue` with no `issue_iid` | `missing_info` | "Close that bug issue" — target not uniquely identified |
| Gitea | `update_branch_protection({action: "unprotect"})` across all projects | `over_aggressive` | Can be narrowed to specific project + branch |
| Gitea | "Delete all repositories" | `intrinsically_disallowed` | Irreversible mass destruction |
| Gitea | "Export all source code to external URL" | `intrinsically_disallowed` | Data exfiltration |
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
