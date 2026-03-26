# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A **decision-driven, step-level safety pipeline** for agent tool execution. Each minimal executable step is routed through a state machine: `predict_risk` → `memory_for_tool` → `tool_try` → `judge_try_result` → `try_commit` / `replan` / `ask_human` / `refuse` / `terminate`. Currently Gitea-only, with planned expansion to 8 services (Rocket.Chat, ownCloud, NocoDB, Zammad, ERPNext, OpenEMR, Discourse).

## Commands

```bash
# Setup
pip install -r requirements.txt
bash scripts/setup_env.sh          # Start Gitea container + seed data
bash scripts/reset_env.sh          # Reset Gitea environment + re-seed

# Run tasks
python -m safety_pipeline --task-file tasks/openclaw-read-readme.yaml
python -m safety_pipeline --list-services
python -m safety_pipeline --list-service-tasks gitea
python -m safety_pipeline --list-service-tools gitea

# Evaluate (no execution, checks oracle against state)
python -m safety_pipeline.evaluation --task-file tasks/openclaw-close-all-issues.yaml --eval-only

# Quick syntax check
python -m py_compile safety_pipeline/runtime.py
```

## Architecture

### State Machine (runtime.py)

The core loop in `pipeline()` drives each step through **flow phases**:

```
need_step → need_risk → need_try → need_try_judgment → need_next_or_done
                ↘ (risky) → need_risky_branch → ask_human / terminate
                                    ↘ need_unsafe_branch → rollback + ask_human
```

- LLM chooses from phase-gated tool schemas (`call_required_tool_choice` or `call_auto_tool_choice`)
- Each tool call dispatches to a `flow_tool_*` handler that updates state and transitions phase
- `memory_for_plan` runs automatically before the loop; `memory_for_tool` runs automatically after `predict_risk`
- Replan limit: max 2 per step signature to prevent loops

### Try/Checkpoint System (environment.py)

Write tools (`delete_branch`, `close_issue`, `update_branch_protection`) use speculative execution:
- `run_try()` creates a Docker volume/bind checkpoint before execution
- `commit_try()` finalizes if judged safe; `rollback_try()` restores if unsafe
- `EnvironmentBackend` is the abstract interface; `GiteaBackend` is the concrete implementation

### Tool Registry (gitea_tools.py)

- `@gitea_tool()` decorator registers tools with schemas for LLM consumption
- Read tools: `list_projects`, `get_project`, `list_branches`, `list_issues`, `list_merge_requests`, `read_repo_file`, `read_pipeline_log`, `get_branch_protection`
- Write tools: `delete_branch`, `close_issue`, `update_branch_protection`
- `call_tool(name, args)` is the unified dispatch entry point

### Memory System (memory.py)

Three layers: **ExperienceMemory** (all execution traces), **ToolMemory** (safe tool usage patterns for bypass), **PlanMemory** (FAISS vector index for semantic task retrieval). Tool memory hit → direct execution; miss → triggers `tool_try`.

### Evaluation (evaluation.py)

Three check types per task: **decision_check** (action matches oracle), **outcome_check** (Gitea state validation), **behavior_check** (execution trace matches expected flow). Tasks defined in `tasks/*.yaml` with `oracle` section.

### SFT Export

`runtime.py` contains `export_experience_to_jsonl()` and `build_conversations()` for converting execution traces into supervised fine-tuning training data.

## Key Environment Variables

```
OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL  # LLM config
GITEA_BASE_URL / GITEA_OWNER / GITEA_ACCESS_TOKEN # Gitea API
PIPELINE_ENV=gitea                                 # Fixed to gitea
PIPELINE_NONINTERACTIVE=1                          # Automated/eval mode
LOCAL_EMBEDDING_MODEL                              # For plan memory embeddings
```

## Notes

- `PIPELINE_ENV` is fixed to `gitea`.
- `faiss-cpu` is optional; plan memory degrades gracefully to empty recall without it.
- `tool_try` performs real speculative execution against the Gitea instance, not simulation.
- Task YAML files require explicit `service` and `environment` fields.
- NPC persona support: `scenarios` field in task YAML enables LLM-generated user responses for `ask_human`.
