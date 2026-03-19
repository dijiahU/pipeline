# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Project Overview

This repository is an agent safety pipeline built around E2B sandboxes and LLM-based decision making.

The current primary implementation is no longer a simple `safe / unsafe` blocker. The main flow in `pipeline.py` is now:

1. first-step routing via `memory_for_plan / ask_human / refuse`
2. `predict_risk`
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

## Current Architecture

### `pipeline.py`

`pipeline.py` is the canonical implementation and should be treated as the source of truth for current behavior.

Important properties:

- It uses explicit step-level decision routing.
- It no longer exposes `thinking_step` as a public tool; first-step intent is carried by `memory_for_plan(tool, tool_args, description)`.
- `predict_risk`, `judge_try_result`, `replan`, and `completion_check` are argument-driven control tools.
- `replan` now emits a single `new_step`, not a `new_steps` array.
- Invalid tool calls can be retried in-loop through `last_tool_error`.
- It persists memory to disk under `memory/`.
- It automatically exports SFT-style samples to `memory/sft_dataset.jsonl` after each run.

Core functions include:

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

This module provides the shared tool registry and MCP entry point. Tools are dynamically discovered and called through the registry rather than hardcoded per tool.

## Memory and Artifacts

The current local artifact layout is:

- `memory/experience_memory.json`
  - raw step-level decision experience
- `memory/tool_memory.json`
  - exact-signature safe-call cache
- `memory/sft_dataset.jsonl`
  - exported SFT-style samples derived from experience memory

`experience_memory.json` is the runtime source of truth.

`sft_dataset.jsonl` is a derived artifact used to inspect and curate training examples. It is currently weakly labeled and should not be described as fully human-curated gold data.

## Decision Semantics

Current action meanings:

- `act`: direct execution for low-side-effect, sufficiently clear steps
- `try`: sandbox-first execution for steps with side effects but clear and verifiable scope
- `replan`: replace the current step with a safer path
- `ask_human`: request clarification, confirmation, or authorization
- `refuse`: reject clearly unacceptable requests

Do not collapse these back into a binary safe/unsafe framing when editing code or docs.

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
   - `memory/tool_memory.json`
   - `memory/sft_dataset.jsonl`

If execution cannot be completed because of network or dependency limits, say so explicitly and still validate syntax plus local artifact behavior where possible.
