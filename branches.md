# Branches for SFT Data

## Safe Main Branches

### 1. Safe + Known Tool + Safe Cache Hit -> Execute Directly

Entry conditions:
- a clear candidate step has been produced
- `predict_risk = safe`
- `memory_for_tool = hit`

### 2. Safe + Known Tool + Cache Miss + Safe Try -> `try_commit`

Entry conditions:
- `predict_risk = safe`
- `memory_for_tool = miss`
- `judge_try_result = safe`

### 3. Safe + Known Tool + Cache Miss + Sandbox Unsafe -> Intercept

Entry conditions:
- `predict_risk = safe`
- `memory_for_tool = miss`
- after `tool_try`, `judge_try_result = unsafe`

## Risky Main Branches

### 4. Risky + No Human Needed + Successful `replan`

Entry conditions:
- `predict_risk = risky`
- the risk comes from the current plan being poor, not from missing information
- the agent can come up with a safer alternative on its own

### 5. Risky + Human Needed -> `ask_human`

Entry conditions:
- `predict_risk = risky`
- the problem is not just a rough plan; it requires information that only the user can provide

### 6. Immediate `refuse`

Entry conditions:
- the user task itself has clearly malicious intent
- no amount of additional information could turn it into a legitimate action

## `ask_human` Follow-Up Branches

### 7. `ask_human` -> User Provides More Information -> Return To Planning

Entry conditions:
- the user is willing to provide more details
- the user reply includes new key information

### 8. `ask_human` -> User Confirms -> Continue Execution

Entry conditions:
- the `ask_human` question is fundamentally a confirmation request rather than an information request
- the user explicitly confirms execution

### 9. `ask_human` -> User Rejects Or Does Not Confirm -> Terminate

Entry conditions:
- the user explicitly refuses
- the user reply indicates that the current task should not continue

## `replan` Follow-Up Branches

### 10. `replan` -> Success -> Output A Safe Plan

Entry conditions:
- `replan` generates a better next step
- after a fresh risk evaluation, the new step enters a safe or otherwise controllable path

### 11. `replan` -> Still Risky -> `ask_human`

Entry conditions:
- even after `replan`, no safe plan is available
- the problem is no longer only "bad plan quality"; it now depends on missing user information

## Unsafe `tool_try` Follow-Up Branches

### 12. Unsafe `tool_try` -> `ask_human` (After Rollback)

Entry conditions:
- `judge_try_result = unsafe`
- safely completing the task still requires additional user input

### 13. Unsafe `tool_try` -> Terminate

Entry conditions:
- `judge_try_result = unsafe`
- no safe alternative exists, or the user does not want to continue
