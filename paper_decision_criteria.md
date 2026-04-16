# Decision Criteria for Step-Level Safety Routing

## Decision Routing Criteria

The current pipeline uses a single structured safety decision before every real-tool step.

Flow:

`predict_risk` -> one of `direct_execute` / `ask_human` / `refuse` / `replan`

The routing rule is evidence-first: the branch must be justified by the concrete target, current scope, available user authorization, and observed runtime context. There is no speculative sandbox execution stage.

## `predict_risk`

`predict_risk` records the model's branch decision for the current candidate real-tool call.

Allowed outputs:

| Result | Meaning |
|---|---|
| `direct_execute` | The current step is concrete enough and acceptable to execute now |
| `ask_human` | One blocking question must be answered first |
| `refuse` | The goal itself should not be helped with |
| `replan` | The current step is too broad or risky, but a safer concrete replacement exists |

## Risk Source Taxonomy

| Risk Source | Required Action |
|---|---|
| Missing information, confirmation, or authorization | `ask_human` |
| Over-broad or poorly scoped plan | `replan` |
| Intrinsically disallowed goal | `refuse` |
| Concrete, sufficiently scoped, acceptable step | `direct_execute` |

## `ask_human`

Use `ask_human` only when all of the following hold:

- the task is still potentially legitimate
- one specific blocking gap remains
- the gap cannot be resolved from tool schemas or prior observations
- a user reply could unblock the next step

Do not use `ask_human` for generic caution or because a tool is unfamiliar.

## `replan`

Use `replan` when:

- the risk comes from the current plan shape rather than from a prohibited end goal
- the agent can propose exactly one safer replacement step
- the replacement still moves the task forward

The replacement step must be re-judged by a fresh `predict_risk` call.

## `refuse`

Use `refuse` when:

- the requested goal is malicious, unauthorized, or fundamentally disallowed
- more user input would not make the goal acceptable

`refuse` is a pre-execution stop. The runtime no longer uses a separate post-speculation stop stage.

## `direct_execute`

Real execution is authorized only through `predict_risk.result=direct_execute`.

When this branch is selected:

- the current real-tool step is executed immediately
- the execution is recorded as `direct_tool`
- the loop either finishes or advances to the next step

## Runtime Context

Historical memory layers have been removed from the runtime.
Scenario adaptation should come from deployment thresholds and branch-confidence gating instead of recalled traces.

There is no per-tool safety cache in the current design.

## Summary Routing Table

| Decision Node | Condition | Next Action |
|---|---|---|
| `predict_risk` | Step is concrete and acceptable | `direct_execute` |
| `predict_risk` | Missing user-specific information or authorization | `ask_human` |
| `predict_risk` | Current plan is too broad but repairable | `replan` |
| `predict_risk` | Goal is intrinsically disallowed | `refuse` |
