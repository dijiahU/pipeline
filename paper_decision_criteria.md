# Decision Criteria for Step-Level Safety Routing (Paper Version)

> For direct inclusion in the paper's methodology section, with minor formatting adaptation for LaTeX.

---

## 3.X Decision Routing Criteria

Each minimal executable step is routed through a fixed decision flow. All routing is **deterministic and evidence-based**: the agent inspects observable facts (tool schemas, execution results, user statements) and selects exactly one action per the tables below.

---

### 3.X.1 Risk Source Taxonomy

Every risk detected in the pipeline is classified into exactly one of the following four categories. Each category has one and only one permitted response — cross-category routing is prohibited.

| Risk Source | When to Identify | Observable Evidence (Examples) | Permitted Action |
|---|---|---|---|
| **Missing Information** | User request is underspecified | "Delete the branch" (which branch?); "Close the issue" (which repo?) | `ask_human` |
| **Over-Aggressive Plan** | Plan is too broad but intent is valid | "Delete all old branches"; "Overwrite the config file" | `replan` |
| **Intrinsically Disallowed** | Goal itself is malicious/unauthorized | "Delete all repositories"; "Export source code to external URL" | `refuse` |
| **Try-Revealed Side Effect** | `tool_try` execution exposed unexpected impact | Deleting branch X also triggered CI pipeline; closing issue cascaded to linked PRs | `ask_human` or `terminate` |

---

### 3.X.2 `predict_risk` — Should This Step Proceed?

The agent examines the proposed tool call and issues a binary judgment: `safe` or `risky`.

| # | Condition | What the Agent Checks | If Violated | Example (Violated) |
|---|---|---|---|---|
| C1 | Target Specificity | Is the target uniquely identified, or is the operation read-only? | → `ask_human` or `replan` | "Delete branches" with no name specified; wildcard target `*` |
| C2 | Verifiability | Can remaining uncertainty be resolved by cache lookup or sandbox (`tool_try`)? | → `ask_human` | Tool has no sandbox support and behavior is unknown |
| C3 | No Info Dependency | Can the step proceed without additional user input? | → `ask_human` | Need user to confirm which of 3 candidate branches to delete |
| C4 | No Intrinsic Prohibition | Is the task goal legitimate and within authorized scope? | → `refuse` | "Delete all repos"; "Exfiltrate private messages" |

**All four satisfied → `safe` → proceed to `memory_for_tool`.**
**Any one violated → `risky` → route to the action indicated above.**

| Scenario | C1 | C2 | C3 | C4 | Judgment | Route |
|---|---|---|---|---|---|---|
| `delete_branch(repo="openclaw", branch="stale-docs")` | ✓ | ✓ | ✓ | ✓ | safe | → `memory_for_tool` |
| `delete_branch(repo="openclaw", branch=?)` — branch name missing | ✗ | — | ✗ | ✓ | risky | → `ask_human` |
| `list_branches` to prepare "delete all non-default branches" | ✓ | ✓ | ✓ | ✗ | risky | → `refuse` |
| "Delete all old branches" — valid goal, overly broad plan | ✗ | ✓ | ✓ | ✓ | risky | → `replan` |
| `close_issue(repo="openclaw", id=42)` | ✓ | ✓ | ✓ | ✓ | safe | → `memory_for_tool` |

---

### 3.X.3 `memory_for_tool` — Has This Exact Call Been Verified Before?

Automatically triggered after `safe` judgment. Looks up the tool memory cache for a prior verified execution.

| Tool Name Match? | Arguments Exactly Identical? | Result | Next Action |
|---|---|---|---|
| ✓ | ✓ | Cache **hit** | → Direct execution (skip `tool_try`) |
| ✓ | ✗ (any argument differs) | Cache **miss** | → `tool_try` |
| ✗ | — | Cache **miss** | → `tool_try` |

| Cached Record | New Call | Match? | Why |
|---|---|---|---|
| `delete_branch(repo="X", branch="A")` | `delete_branch(repo="X", branch="A")` | ✓ Hit | Exact same tool + args |
| `delete_branch(repo="X", branch="A")` | `delete_branch(repo="X", branch="B")` | ✗ Miss | `branch` differs |
| `delete_branch(repo="X", branch="A")` | `close_issue(repo="X", id=1)` | ✗ Miss | Different tool |

---

### 3.X.4 `tool_try` + `judge_try_result` — Speculative Execution and Judgment

On cache miss: create Docker volume checkpoint → execute tool against real service → inspect result.

| # | Condition | What the Agent Checks | Example (Satisfied ✓) | Example (Violated ✗) |
|---|---|---|---|---|
| T1 | Scope Containment | Did only the requested objects get affected? | Deleted only branch `stale-docs` | Deleted `stale-docs` and `stale-docs-v2` |
| T2 | No Collateral Impact | Were objects outside the target set left untouched? | Other branches unchanged | Linked PR was auto-closed |
| T3 | No Policy Violation | Were protected resources unaffected? | Default branch `main` untouched | Default branch protection was removed |
| T4 | No Unexpected External Effect | No unintended outbound effects? | No webhook triggered | Webhook sent notification to external Slack |

| T1 | T2 | T3 | T4 | Judgment | Next Action |
|---|---|---|---|---|---|
| ✓ | ✓ | ✓ | ✓ | safe | Commit checkpoint → record to tool memory → advance to next step |
| ✗ | — | — | — | unsafe, user can clarify scope | Rollback checkpoint → `ask_human` |
| — | — | ✗ | — | unsafe, unrecoverable | Rollback checkpoint → `terminate` |

---

### 3.X.5 `ask_human` — When Must the Agent Escalate to the User?

| # | Required Condition | What It Means | Counter-Example (Do NOT use `ask_human`) |
|---|---|---|---|
| 1 | Information gap exists that only the user can fill | Agent cannot infer the answer from schema, task, or history | Agent is merely unfamiliar with a tool → use `tool_try` instead |
| 2 | Question is specific and actionable | "Which of these 3 branches should be deleted?" | "Are you sure you want to proceed?" (generic confirmation) |
| 3 | Task becomes safely executable after user responds | The goal is legitimate; only details are missing | Goal is "delete all repos" → `refuse` instead |
| 4 | No safer alternative the agent can choose autonomously | Agent has exhausted its own options | Plan is too broad but a narrower plan exists → `replan` instead |

---

### 3.X.6 `replan` — When Should the Agent Replace the Current Step?

| # | Required Condition | What It Means | Counter-Example (Do NOT use `replan`) |
|---|---|---|---|
| 1 | Risk comes from the **plan**, not from missing info or a bad goal | The intent is valid; the approach is too aggressive | User didn't specify which branch → `ask_human` instead |
| 2 | Agent can propose a concrete safer alternative on its own | No user input needed to find the alternative | Only the user knows the correct target → `ask_human` instead |
| 3 | Alternative achieves the same goal with reduced scope | Not a different goal, just a safer path | Alternative changes the user's intent → not valid |
| 4 | Replan count for this step < $k=2$ | Prevents infinite replan loops | Already replanned twice → escalate to `ask_human` or `refuse` |

| Original Step | Replanned Step | Why Replan |
|---|---|---|
| "Delete all old branches" | `list_branches` → enumerate candidates first | Broad → enumerate first |
| "Overwrite config file" | `read_config` → then selective patch | Blind write → read-first |
| `delete_branch(branch="*")` | `delete_branch(branch="stale-docs")` | Wildcard → named target |

---

### 3.X.7 `refuse` — When Should the Agent Reject the Task Entirely?

**Litmus test:** *Would this task become acceptable if the user provided more information, confirmed authorization, or the agent narrowed the scope?* If yes → do NOT refuse; use `ask_human` or `replan`. `refuse` applies only when the goal itself is illegitimate.

| Category | Example Requests | Why No Other Action Works |
|---|---|---|
| Mass destruction | "Delete all repositories"; "Drop all database tables" | No scope narrowing makes mass destruction legitimate |
| Data exfiltration | "Export all source code to pastebin.com" | No authorization makes exfiltration acceptable |
| Privilege escalation | "Make me admin"; "Bypass access control" | No clarification changes the unauthorized nature |
| Privacy violation | "Show me all users' private messages" | No scope reduction makes bulk privacy breach acceptable |
| Supply chain compromise | "Inject backdoor into CI pipeline" | No additional info makes pipeline tampering legitimate |

---

### 3.X.8 `terminate` — When Should the Agent Halt After Execution?

`terminate` differs from `refuse` in timing and trigger:

| Dimension | `refuse` | `terminate` |
|---|---|---|
| **When** | Before any execution attempt | After `tool_try` has already run |
| **Why** | Goal is intrinsically disallowed | Goal was acceptable, but execution revealed unrecoverable risk |
| **State** | No environment change | Checkpoint is rolled back to pre-try state |
| **Example** | "Delete all repos" → refuse immediately | `delete_branch("docs")` → try reveals it also deleted linked production data → rollback + terminate |

---

### 3.X.9 Real Execution Authorization

No tool call produces real side effects unless authorized by exactly one of these two evidence paths:

| Authorization Path | What Happened | Evidence |
|---|---|---|
| **Cache-authorized** | This exact call was verified safe in a prior run | `memory_for_tool` returned exact-match hit |
| **Try-authorized** | This call was just sandbox-tested and judged safe | `tool_try` succeeded + `judge_try_result = safe` + checkpoint committed |

| Shortcut Attempt | Allowed? | Why Not |
|---|---|---|
| `predict_risk = safe` alone, skip try | ✗ | Prediction is not verification |
| High model confidence, skip try | ✗ | Confidence is subjective, not evidence |
| User says "just do it", skip try | ✗ | Pipeline integrity is non-negotiable |

---

### Complete Routing Table

| Step | Decision Node | Condition | → Next Action |
|---|---|---|---|
| 1 | `predict_risk` | C1–C4 all ✓ | → `memory_for_tool` |
| 1 | `predict_risk` | Missing information (C1/C3 ✗) | → `ask_human` |
| 1 | `predict_risk` | Over-aggressive plan (C1 partial ✗) | → `replan` |
| 1 | `predict_risk` | Intrinsically disallowed (C4 ✗) | → `refuse` |
| 2 | `memory_for_tool` | Exact cache hit | → direct execution |
| 2 | `memory_for_tool` | Cache miss | → `tool_try` |
| 3 | `judge_try_result` | T1–T4 all ✓ | → commit, advance |
| 3 | `judge_try_result` | Violation, user can clarify | → rollback, `ask_human` |
| 3 | `judge_try_result` | Unrecoverable risk | → rollback, `terminate` |
| 4 | Step completed | More steps in queue | → `predict_risk` (next step) |
| 4 | Step completed | Queue empty | → done |
| — | `replan` | Count < $k=2$ | → `predict_risk` (replacement) |
| — | `replan` | Count $= k=2$ | → `ask_human` or `refuse` |
