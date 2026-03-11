# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Project Overview

This repository is an agent safety pipeline built around E2B sandboxes and LLM-based decision making.

The current primary implementation is no longer a simple `safe / unsafe` blocker. The main flow in `pipeline.py` is now:

1. `generate_plan`
2. `detect_step_risk`
3. `decide_action`
4. route to one of `act / try / replan / ask_human / refuse`
5. write local memory and refresh exported SFT samples

The goal of the current system is to make step-level action choices, not just to block dangerous plans.

## Commands

```bash
pip install -r requirements.txt
python pipeline.py
python pipeline.py --task "删除 /home/user 下所有 .log 文件"
python pipeline_langchain.py
python mcp_tools.py
python -m py_compile pipeline.py pipeline_langchain.py mcp_tools.py
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
- It distinguishes `think` from `reflect`:
  - `think`: fact-level analysis
  - `reflect`: action-level inclination
- It persists memory to disk under `memory/`.
- It automatically exports SFT-style samples to `memory/sft_dataset.jsonl` after each run.

Core functions include:

- `generate_plan()`
- `detect_step_risk()`
- `decide_action()`
- `replan_step()`
- `tool_try_in_sandbox()`
- `judge_safety()`
- `record_experience()`
- `export_experience_to_jsonl()`
- `persist_local_artifacts()`

### `pipeline_langchain.py`

This is the LangChain/LangGraph variant. It is still useful as a future graph-based target, but it does not yet fully mirror the latest `pipeline.py` behavior. When in doubt, align docs and behavior with `pipeline.py` first.

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

1. `python -m py_compile pipeline.py pipeline_langchain.py mcp_tools.py`
2. a manual `python pipeline.py --task "..."` run when dependencies are available
3. inspection of:
   - `memory/experience_memory.json`
   - `memory/tool_memory.json`
   - `memory/sft_dataset.jsonl`

If execution cannot be completed because of network or dependency limits, say so explicitly and still validate syntax plus local artifact behavior where possible.
