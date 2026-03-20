# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This repository is an agent safety pipeline built around E2B sandboxes and LLM-based decision making.

The current primary implementation is no longer a simple `safe / unsafe` blocker. The main flow in `pipeline.py` is now:

1. first-step routing via `memory_for_plan`（纯检索，无参数）/ `ask_human` / `refuse`
2. `predict_risk`（带 step 和风险判断）
3. safe path: `memory_for_tool -> tool_try -> judge_try_result -> direct_tool`
4. risky or failed path: `replan / ask_human / refuse / terminate`
5. `completion_check`
6. write local memory and refresh exported SFT samples

The goal of the current system is to make step-level action choices, not just to block dangerous plans.

## Commands

```bash
pip install -r requirements.txt
python pipeline.py
python pipeline.py --task "删除 /home/user 下所有 .log 文件"
python mcp_tools.py
python -m py_compile pipeline.py mcp_tools.py
```

Required environment variables for full pipeline execution:

- `E2B_API_KEY`
- `OPENAI_API_KEY`

Pure local reads of `memory/` artifacts do not require live API access.

## Key Constants

```python
OPENAI_MODEL = "gpt-5.2"               # overridable via env var OPENAI_MODEL
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
MAX_AGENT_TOOL_ROUNDS = 40             # main loop iteration cap
MAX_TOOL_CALL_RETRIES = 3              # retries per invalid tool call
MAX_STEP_REPLAN = 2                    # replan attempts before escalating
MAX_CONVERSATION_TURNS = 8
PLAN_MEMORY_TOP_K = 6                  # nearest neighbors for task retrieval
```

## Current Architecture

### `pipeline.py`

`pipeline.py` is the canonical implementation and should be treated as the source of truth for current behavior.

Important properties:

- It uses explicit step-level decision routing.
- `memory_for_plan` is a 0-parameter pure retrieval tool; it recalls similar prior user tasks based on the current task, not steps.
- `predict_risk` carries both the current step (`tool`, `tool_args`, `description`) and the risk judgment (`result`, `reasoning`, `likely_next_action`, `criterion_hits`).
- `predict_risk`, `judge_try_result`, `replan`, and `completion_check` are argument-driven control tools.
- `replan` now emits a single `new_step`, not a `new_steps` array.
- Invalid tool calls can be retried in-loop through `last_tool_error`.
- It persists memory to disk under `memory/`.
- It automatically exports SFT-style samples to `memory/sft_dataset.jsonl` after each run.

#### Flow Phases (state machine)

The main loop dispatches tools based on `state["flow_phase"]`. The phase transitions are:

1. `need_step` → agent calls `memory_for_plan` (0 params, pure retrieval) or `ask_human` / `refuse`
2. `need_risk` → agent calls `predict_risk` (carries step + risk judgment)
3. `check_memory` → agent calls `memory_for_tool` (cache lookup)
4. `tool_try` → sandbox execution via `tool_try`
5. `judge_try` → agent calls `judge_try_result`
6. `replan` → agent calls `replan` (generates `new_step`)
7. `ask_human` → agent calls `ask_human`
8. `completion` → agent calls `completion_check`

Only tools valid for the current phase are exposed to the LLM at each step.

#### Flow Tools vs Real Tools

- **Flow tools** (`memory_for_plan`, `predict_risk`, `memory_for_tool`, `judge_try_result`, `replan`, `ask_human`, `refuse`, `terminate`, `completion_check`): argument-driven control tools that steer the pipeline. Exported to SFT format.
- **Real tools** (`run_python_code`, `run_shell_command`, `read_file`, `write_file`, `list_files`, `delete_file`, `send_http_request`): execute actual side effects in the E2B sandbox. Only called after risk assessment passes.

#### Core functions

- `flow_tool_memory_for_plan()`
- `flow_tool_predict_risk()`
- `flow_tool_try()`
- `flow_tool_judge_try_result()`
- `flow_tool_replan()`
- `flow_tool_completion_check()`
- `record_experience()`
- `export_experience_to_jsonl()`
- `persist_local_artifacts()`

### `mcp_tools.py`

This module provides the shared tool registry and MCP entry point. Tools are dynamically discovered via the `@tool()` decorator and called through `call_tool()` rather than hardcoded per tool.

## Memory and Artifacts

The current local artifact layout is:

- `memory/experience_memory.json`
  - raw step-level decision experience
- `memory/plan_memory_index.json`
  - task-level embedding index derived from experience memory
- `memory/tool_memory.json`
  - exact-signature safe-call cache
- `memory/sft_dataset.jsonl`
  - exported SFT-style samples derived from experience memory

`experience_memory.json` is the runtime source of truth.

Newer cases store compact `risk` objects (`level`, `reason`, `next_action`, `criteria`) instead of the older `risk_assessment` shape. Read code and exports with backward compatibility in mind.

`sft_dataset.jsonl` is a derived artifact used to inspect and curate training examples. It is currently weakly labeled and should not be described as fully human-curated gold data.

The exported tool-calling format now includes:

- `system`
- `tool_groups`
  - `shared_flow_tools`
  - `task_tools`
- `tools`
- `conversations`

`tools` is the flattened list used by trainers; `tool_groups` is the readable split between flow-control tools and task-specific real tools.

For export semantics:

- `ask_human` with a real user reply should be serialized as `function_call(ask_human) -> human(...)`, not `observation.human_reply` plus another `human` turn.
- `completion_check.status=done` should append a final `gpt` reply using the tool's `reply` field.

## Decision Semantics

Current action meanings:

- `act`: direct execution for low-side-effect, sufficiently clear steps
- `try`: sandbox-first execution for steps with side effects but clear and verifiable scope
- `replan`: replace the current step with a safer path
- `ask_human`: request clarification, confirmation, or authorization
- `refuse`: reject clearly unacceptable requests

Do not collapse these back into a binary safe/unsafe framing when editing code or docs.

See `criterion.md` for detailed judgment rules per flow tool, and `branches.md` for the full SFT data split taxonomy (14 decision paths).

## Working Guidance

When modifying the project:

- prefer updating `pipeline.py` first, because it is the more transparent implementation;
- keep logs readable by phase, since the current design expects a human to understand each stage;
- preserve local memory persistence and automatic SFT export unless the task explicitly asks to change them;
- avoid describing old concepts like `PlanMemory` as the active design if they no longer exist in code.

## Validation

For changes to current behavior, validate with:

1. `python -m py_compile pipeline.py mcp_tools.py`
2. a manual `python pipeline.py --task "..."` run when dependencies are available
3. inspection of:
   - `memory/experience_memory.json`
   - `memory/plan_memory_index.json`
   - `memory/tool_memory.json`
   - `memory/sft_dataset.jsonl`

If execution cannot be completed because of network or dependency limits, say so explicitly and still validate syntax plus local artifact behavior where possible.

There are no automated tests. Manual test scenarios are documented at the bottom of `pipeline.py` as comments (12 scenarios covering safe/risky paths, cache hits, replan chains, ask_human flows, etc.).
