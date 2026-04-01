# Decision Criteria for Step-Level Safety Routing (Paper Version)

> This section is intended for direct inclusion in the paper's methodology section, with minor formatting adaptation for LaTeX.

---

## Decision Routing Criteria

Our pipeline enforces a fixed decision flow where each minimal executable step is routed through a sequence of structured decision nodes. All routing decisions follow the **Evidence-Before-Action Principle**: decisions must be based on observable evidence (user statements, tool schemas, memory retrieval results, or sandbox observations), not on model confidence or general caution.

### Risk Source Taxonomy

Four mutually exclusive risk source categories. Each maps deterministically to a downstream action; cross-category routing is prohibited.

| Risk Source | Definition | Required Action |
|---|---|---|
| **Missing Information** | The request lacks user-specific information (target identity, scope boundary, or authorization) that cannot be inferred from context | `ask_human` |
| **Over-Aggressive Plan** | The proposed step is valid in intent but too broad or insufficiently controlled; a safer alternative exists | `replan` |
| **Intrinsically Disallowed** | The task goal itself is malicious, unauthorized, or policy-violating; no additional information makes it acceptable | `refuse` |
| **Try-Revealed Side Effect** | Speculative execution revealed effects outside the expected scope | `ask_human` or `terminate` |

### `predict_risk`

Binary risk assessment (`safe` / `risky`). A step is `safe` if and only if **all four** conditions hold:

| Condition | Description | Violation → Risk Source |
|---|---|---|
| **(C1)** Target Specificity | The target object is uniquely identified (e.g., a named branch, a specific issue ID), or the operation is read-only with no side effects | Missing Information / Over-Aggressive Plan |
| **(C2)** Verifiability | Residual uncertainty can be resolved through tool memory cache or speculative sandbox execution (`tool_try`) | — |
| **(C3)** No Information Dependency | The step does not require additional user input to proceed | Missing Information |
| **(C4)** No Intrinsic Prohibition | The task goal is legitimate and falls within the agent's authorized scope | Intrinsically Disallowed |

**Boundary cases:**

| Case | Judgment | Reason |
|---|---|---|
| Write operation on a uniquely identified target (e.g., `delete_branch(repo="openclaw", branch="stale-docs")`) | `safe` | C1–C4 satisfied; actual safety verified via `tool_try` |
| Read-only step facilitating a downstream bulk-destructive goal (e.g., `list_branches` to prepare "delete all non-default branches") | `risky` | C4 violated at intent level |
| Unfamiliar tool with well-defined schema and valid arguments | `safe` | C1–C4 satisfied; uncertainty resolved via C2 (`tool_try`) |

###  `memory_for_tool`

Automatically triggered after `predict_risk = safe`. Queries the tool memory for an exact-match prior verification.

| Condition | Next Action |
|---|---|
| **Cache hit**: exact match on tool name AND complete argument object | Direct execution (bypass `tool_try`) |
| **Cache miss**: any argument differs or no prior record | `tool_try` |

No fuzzy matching, semantic similarity, or cross-argument transfer. A cached `delete_branch(repo="X", branch="A")` does **not** validate `delete_branch(repo="X", branch="B")`.

###  `tool_try` + `judge_try_result`

On cache miss, the system creates a Docker volume checkpoint and executes the tool call against the real service. The result is judged `safe` if and only if **all four** conditions hold:

| Condition | Description |
|---|---|
| **(T1)** Scope Containment | All affected objects fall within the explicitly requested scope |
| **(T2)** No Collateral Impact | No objects outside the target set were modified, deleted, or accessed |
| **(T3)** No Policy Violation | No protected resources (default branches, system-critical data) were affected |
| **(T4)** No Unexpected External Effect | No unintended outbound requests, permission changes, or cascading triggers were observed |

**Routing after judgment:**

| Judgment | Condition | Next Action |
|---|---|---|
| `safe` | T1–T4 all satisfied | Commit checkpoint, record to tool memory, advance |
| `unsafe` | Scope/policy violation, user can clarify | Rollback checkpoint → `ask_human` |
| `unsafe` | Unrecoverable risk, no human path | Rollback checkpoint → `terminate` |

###  `ask_human`

| Condition | Satisfied? |
|---|---|
| Safe completion depends on information only the user can provide | **Required** |
| Missing info cannot be inferred from tool schema, task description, or execution history | **Required** |
| The question posed is specific and actionable (not generic confirmation) | **Required** |
| Task goal would become safely executable once user responds | **Required** |
| Task goal is not intrinsically disallowed | **Required** |

**Exclusion rules** (must NOT use `ask_human`):

| Situation | Correct Action Instead |
|---|---|
| Model uncertainty about unfamiliar tools | `tool_try` |
| Generic caution without evidence of a specific gap | Proceed as `safe` |
| Over-aggressive plan with clear safer alternative | `replan` |
| Intrinsically disallowed goal | `refuse` |

###  `replan`

| Condition | Satisfied? |
|---|---|
| Risk originates from the **plan**, not from missing user info or a prohibited goal | **Required** |
| Agent can autonomously propose a concrete, safer alternative | **Required** |
| Alternative achieves the same user goal with reduced scope or risk | **Required** |
| Replan count for this step signature < $k=2$ | **Required** |

**Replan cap enforcement:**

| Replan Count | Next Action |
|---|---|
| < $k=2$ | `predict_risk` on replacement step |
| $= k=2$ | Escalate to `ask_human` or `refuse` |

**Typical replan patterns:**

| Original Plan | Replanned Step | Rationale |
|---|---|---|
| Batch delete all old branches | `list_branches` to enumerate candidates | Broad → enumerate first |
| Overwrite config file | Read current config, then selective patch | Write-first → read-first |
| Wildcard target deletion | Single explicit-target deletion | Wildcard → named target |

###  `refuse`

Reserved for requests whose goals are **intrinsically disallowed**. Before issuing `refuse`, the agent must verify: *Would this task become acceptable if the user provided additional information, confirmed authorization, or the agent narrowed the scope?* If yes → use `ask_human` or `replan` instead.

| Category | Examples |
|---|---|
| Mass destruction | Delete all repositories; drop all database tables; wipe all files |
| Data exfiltration | Export source code to external URLs; email confidential data to outside parties |
| Privilege escalation | Bypass access controls; impersonate other users; escalate token scopes |
| Privacy violation | Bulk extract private messages; access other users' private repositories |
| Supply chain compromise | Inject code into CI/CD pipelines; tamper with release artifacts |

###  `terminate`

Post-execution halt. Distinguished from `refuse`:

| | `refuse` | `terminate` |
|---|---|---|
| **Timing** | Before any execution | After `tool_try` |
| **Trigger** | Goal is intrinsically disallowed | Goal was acceptable, but execution revealed unrecoverable risk |
| **Environment** | No state change | Checkpoint rolled back to pre-try state |

###  Real Execution Authorization

Real tool execution (producing actual side effects) is authorized exclusively through two paths:

| Path | Required Evidence |
|---|---|
| **Cache-authorized** | `memory_for_tool` exact-match hit from a previously verified safe execution |
| **Try-authorized** | `tool_try` completed + `judge_try_result = safe` + checkpoint committed |

**Prohibited shortcuts:**

| Shortcut | Why Prohibited |
|---|---|
| Safe risk prediction alone | Prediction is not verification |
| Model confidence | Subjective, not evidence-based |
| User instruction to skip checks | Pipeline integrity is non-negotiable |

---

### Summary: Complete Routing Table

| Decision Node | Condition | Next Action |
|---|---|---|
| `predict_risk` | C1–C4 all satisfied | → `memory_for_tool` |
| `predict_risk` | Missing information (C1 or C3 violated) | → `ask_human` |
| `predict_risk` | Over-aggressive plan (C1 partially violated, safer alternative exists) | → `replan` |
| `predict_risk` | Intrinsically disallowed (C4 violated) | → `refuse` |
| `memory_for_tool` | Exact cache hit | → direct execution |
| `memory_for_tool` | Cache miss | → `tool_try` |
| `judge_try_result` | T1–T4 all satisfied | → commit checkpoint, advance |
| `judge_try_result` | Scope/policy violation, user can clarify | → rollback, `ask_human` |
| `judge_try_result` | Unrecoverable risk, no human path | → rollback, `terminate` |
| Step completed | More steps in queue | → `predict_risk` (next step) |
| Step completed | Queue empty, task fulfilled | → natural language reply (done) |
| `replan` | Cap not reached, alternative exists | → `predict_risk` (replacement step) |
| `replan` | Cap ($k=2$) reached | → `ask_human` or `refuse` |
