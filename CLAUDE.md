# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This repository is an agent safety pipeline built around pluggable service backends (GitLab API, future: RocketChat, etc.) and LLM-based decision making.

The current primary implementation is no longer a simple `safe / unsafe` blocker. The main flow in `safety_pipeline/runtime.py` is now:

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
python -m safety_pipeline --task "列出所有 GitLab 项目"
python -m safety_pipeline --task "删除 sotopia 项目的 feature-test 分支"
python -m safety_pipeline --task-file tasks/safety-delete-repos.yaml
python -m safety_pipeline.evaluation --task-file tasks/safety-close-all-issues.yaml
python -m py_compile safety_pipeline/*.py
```

Environment setup (requires Docker):
```bash
docker compose up -d && bash scripts/setup_env.sh
```

Reset GitLab environment (restore initial data + renew token):
```bash
bash scripts/reset_env.sh
```

Environment variables are configured via `.env` file in the project root (auto-loaded by `settings.py`):

```
OPENAI_API_KEY=your_openai_api_key
OPENAI_BASE_URL=https://openrouter.ai/api/v1   # optional, supports OpenRouter/DeepSeek etc.
OPENAI_MODEL=openai/gpt-4o                       # optional, default gpt-5.2
GITLAB_BASE_URL=http://localhost:8929
GITLAB_ACCESS_TOKEN=root-token
```

Pure local reads of `memory/` artifacts do not require live API access.

## Key Constants

```python
OPENAI_MODEL = "gpt-5.2"               # overridable via env var OPENAI_MODEL
LOCAL_EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
PIPELINE_ENV = "gitlab"                # overridable via env var or --env
MAX_AGENT_TOOL_ROUNDS = 40             # main loop iteration cap
MAX_TOOL_CALL_RETRIES = 3              # retries per invalid tool call
MAX_STEP_REPLAN = 2                    # replan attempts before escalating
MAX_CONVERSATION_TURNS = 8
PLAN_MEMORY_TOP_K = 6                  # nearest neighbors for task retrieval
```

## Current Architecture

### `safety_pipeline/runtime.py`

`safety_pipeline/runtime.py` is the canonical orchestration implementation and should be treated as the source of truth for current behavior.

Important properties:

- It uses explicit step-level decision routing.
- `memory_for_plan` and `memory_for_tool` are both auto-executed by code, not exposed as flow tools to the model. Their results are injected into state and SFT exports.
- `memory_for_plan` recalls similar prior task trajectories at session level (complete tool chains, not individual steps).
- `memory_for_tool` matches by tool name (not exact argument signature), returning up to 2 recent safe call records. Hit skips `tool_try`; miss enters sandbox execution.
- `predict_risk` carries both the current step (`tool`, `tool_args`, `description`) and the risk judgment (`result`, `reasoning`, `likely_next_action`, `criterion_hits`). When `result=safe`, it auto-executes `memory_for_tool` inline.
- `predict_risk`, `judge_try_result`, `replan`, and `completion_check` are argument-driven control tools.
- `replan` now emits a single `new_step`, not a `new_steps` array.
- Invalid tool calls can be retried in-loop through `last_tool_error`.
- It persists memory to disk under `memory/`.
- It automatically exports SFT-style samples to `memory/sft_dataset.jsonl` (full trajectory) and `memory/sft_dataset_stepwise.jsonl` (per-step) after each run.
- `state["tool_call_counter"]` provides a global per-session call index that increments with every tool invocation (flow or real).
- Real tool execution and try logic are delegated to the environment backend via `get_environment_backend()`.
- `--task-file` loads YAML task definitions from `tasks/`, including NPC scenario config.
- When `state["npc_scenario"]` is set, `flow_tool_ask_human()` generates LLM-based NPC replies instead of calling `input()`.
- `load_task_file()` parses YAML and extracts `task`, `environment`, `scenarios`.

#### Flow Phases (state machine)

The main loop dispatches tools based on `state["flow_phase"]`. The phase transitions are:

1. `need_step` → agent calls `ask_human` / `refuse` (memory_for_plan auto-executed before main loop)
2. `need_risk` → agent calls `predict_risk` (carries step + risk judgment; if safe, auto-executes memory_for_tool inline)
3. `need_try` → backend `run_try()` (preview or sandbox execution, only when memory_for_tool misses)
4. `need_try_judgment` → agent calls `judge_try_result`
5. `need_real_tool` → agent calls `direct_tool` (when memory_for_tool hits or judge_try_result=safe)
6. `need_risky_branch` → agent calls `replan` / `ask_human` / `refuse`
7. `need_unsafe_branch` → agent calls `replan` / `ask_human` / `terminate`
8. `need_completion` → agent calls `completion_check`

Only tools valid for the current phase are exposed to the LLM at each step.

#### Flow Tools vs Real Tools

- **Auto-executed tools** (`memory_for_plan`, `memory_for_tool`): executed by code, not by the model. Results injected into state. SFT export synthesizes their function_call + observation turns.
- **Flow tools** (`predict_risk`, `judge_try_result`, `replan`, `ask_human`, `refuse`, `terminate`, `completion_check`): argument-driven control tools that steer the pipeline. Exported to SFT format.
- **Real tools**: execute actual side effects via the active environment backend. Only called after risk assessment passes.
  - GitLab backend: `list_projects`, `get_project`, `list_branches`, `list_issues`, `list_merge_requests`, `read_repo_file`, `read_pipeline_log`, `get_branch_protection`, `preview_delete_branches`, `preview_close_issues`, `delete_branch`, `close_issue`, `update_branch_protection`

#### Core functions

- `get_environment_backend()` — returns the active `EnvironmentBackend`
- `flow_tool_memory_for_plan()` — auto-executed before main loop
- `flow_tool_predict_risk()` — includes auto-execution of `memory_for_tool` when safe
- `flow_tool_try()`
- `flow_tool_judge_try_result()`
- `flow_tool_replan()`
- `flow_tool_completion_check()`
- `record_experience()` — simplified to 4 params: `state, step, final_action, outcome`
- `export_experience_to_jsonl()` — full-trajectory SFT export
- `export_stepwise_to_jsonl()` — per-step SFT export (step N = context from steps 0..N-1 + target step N)
- `persist_local_artifacts()`

### `safety_pipeline/environment.py`

Defines the `EnvironmentBackend` abstraction and concrete backends:

- `GitLabBackend`: wraps `safety_pipeline/gitlab_tools.py` for GitLab API operations

The factory function `get_backend(env_name)` returns the appropriate singleton. `safety_pipeline/runtime.py` calls `get_environment_backend()` which delegates to `get_backend(...)`.

For `tool_try`, GitLabBackend maps write-operation tools to their preview counterparts (e.g., `delete_branch` → `preview_delete_branches`). Read-only tools are executed directly since they have no side effects.

Future backends (RocketChat, filesystem, etc.) follow the same pattern.

### `safety_pipeline/gitlab_tools.py`

Service-oriented tool module for GitLab API. Uses `@gitlab_tool()` decorator and `_REGISTRY` pattern. This is the standard pattern for all service integrations.

Current tools are grouped into read-only, preview, and write tiers:
- **Read-only (8)**: `list_projects`, `get_project`, `list_branches`, `list_issues`, `list_merge_requests`, `read_repo_file`, `read_pipeline_log`, `get_branch_protection`
- **Preview**: `preview_delete_branches`, `preview_close_issues`, `preview_close_issue`, `preview_update_branch_protection`
- **Write (3)**: `delete_branch`, `close_issue`, `update_branch_protection`

### `safety_pipeline/evaluation.py`

Task-level evaluation framework. Three check types:

- **decision_check**: verifies the pipeline's final decision matches `oracle.preferred_action` in the task YAML.
- **outcome_check**: calls GitLab API after the run to verify state (e.g., all issues still open, branches still protected).
- **behavior_check**: (planned) verifies the pipeline walked the correct flow path.

Entry point: `python -m safety_pipeline.evaluation --task-file tasks/safety-close-all-issues.yaml`

### `tasks/*.yaml`

YAML task definitions for evaluation. Each file specifies:
- `id`, `environment`, `task` (the user prompt)
- `oracle.preferred_action` (expected decision: `refuse`, `execute`, `ask_human`)
- `oracle.outcome_check` (optional GitLab API post-condition)
- `scenarios` (optional NPC config for simulated user interaction)

### `docker-compose.yml` / `scripts/setup_env.sh`

- `docker-compose.yml`: single-service compose for the pre-built GitLab image.
- `scripts/setup_env.sh`: starts containers and polls until GitLab API is healthy.

## GitLab Environment

The pipeline targets a Docker-hosted GitLab instance (from OpenAgentSafety). Key facts:

- Image: `ghcr.io/theagentcompany/servers-gitlab:1.0.0` — pre-built with 6+ projects (sotopia, openhands, etc.)
- Default access: `http://localhost:8929` with admin token `root-token`
- Start: `docker compose up -d && bash scripts/setup_env.sh`
- Reset: `bash scripts/reset_env.sh` (restores initial data + renews token)
- Data is baked into the image — no per-task data generation needed

## Memory and Artifacts

The current local artifact layout is:

- `memory/experience_memory.json`
  - step-level decision experience, each record contains 9 fields: `task, turn_id, step_index, dialogue_snapshot, flow_tool_calls, step, decision, outcome, memory_id`
  - `flow_tool_calls` is an array of `{call_index, phase, tool_name, arguments, result}`, capturing every tool invocation with global call_index
- `memory/plan_memory.faiss`
  - FAISS vector index derived from experience memory, indexed at trajectory (session) level
- `memory/plan_memory_meta.json`
  - metadata aligned with the FAISS vector index, each entry represents a complete task session
- `memory/tool_memory.json`
  - exact-signature safe-call cache
- `memory/sft_dataset.jsonl`
  - full-trajectory SFT samples (one sample per session)
- `memory/sft_dataset_stepwise.jsonl`
  - per-step SFT samples (step N uses steps 0..N-1 as context, step N as target), with `meta` field for filtering

`experience_memory.json` is the runtime source of truth. Old records may have extra top-level fields (`plan_memory`, `risk`, `tool_memory`, etc.) which are now removed — all information is captured in `flow_tool_calls`. Export logic has legacy fallback for old-format data.

Both SFT datasets are derived artifacts used to inspect and curate training examples. They are currently weakly labeled and should not be described as fully human-curated gold data.

The exported tool-calling format includes:

- `system`
- `tools` (JSON string, each tool wrapped as `{type: "function", function: {...}}`)
- `conversations` (multi-turn dialogue in LlamaFactory ShareGPT/tool-calling format)

For export semantics:

- `memory_for_plan` and `memory_for_tool` are auto-executed at runtime but injected into SFT data as `function_call({}) + observation` turns, making them look like model-initiated calls.
- `ask_human` with a real user reply should be serialized as `function_call(ask_human) -> human(...)`, not `observation.human_reply` plus another `human` turn.
- `completion_check.status=done` should append a final `gpt` reply using the tool's `reply` field.

## Decision Semantics

Current action meanings:

- `act`: direct execution for low-side-effect, sufficiently clear steps
- `try`: preview/sandbox-first execution for steps with side effects but clear and verifiable scope
- `replan`: replace the current step with a safer path
- `ask_human`: request clarification, confirmation, or authorization
- `refuse`: reject clearly unacceptable requests

Do not collapse these back into a binary safe/unsafe framing when editing code or docs.

See `criterion.md` for detailed judgment rules per flow tool, and `branches.md` for the full SFT data split taxonomy (14 decision paths).

## Working Guidance

When modifying the project:

- prefer updating the package modules under `safety_pipeline/`;
- keep logs readable by phase, since the current design expects a human to understand each stage;
- preserve local memory persistence and automatic SFT export unless the task explicitly asks to change them;
- new service backends should follow the `safety_pipeline/gitlab_tools.py` + `EnvironmentBackend` pattern.

## Validation

For changes to current behavior, validate with:

1. `python -m py_compile safety_pipeline/*.py`
2. a manual `python -m safety_pipeline --task "..."` run when dependencies are available
3. task-file mode: `python -m safety_pipeline --task-file tasks/safety-list-projects.yaml`
4. evaluator: `python -m safety_pipeline.evaluation --task-file tasks/safety-close-all-issues.yaml`
5. inspection of:
   - `memory/experience_memory.json`
   - `memory/plan_memory.faiss`
   - `memory/plan_memory_meta.json`
   - `memory/tool_memory.json`
   - `memory/sft_dataset.jsonl`
   - `memory/sft_dataset_stepwise.jsonl`

If execution cannot be completed because of network or dependency limits, say so explicitly and still validate syntax plus local artifact behavior where possible.

There are no automated tests. Manual test scenarios are documented at the bottom of `safety_pipeline/runtime.py` as comments (GitLab scenarios covering safe/risky paths, cache hits, replan chains, ask_human flows, etc.). Task YAML files in `tasks/` serve as the primary evaluation scenarios.

> 查看上一次修改详情：[changes.md](./changes.md)
