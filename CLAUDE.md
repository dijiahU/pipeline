# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

An agent safety pipeline for multi-service admin tasks. At each step, the runtime chooses one of four decisions: `direct_execute`, `ask_human`, `refuse`, or `replan`. The system supports 9 service backends (Gitea, Rocket.Chat, ownCloud, NocoDB, Zammad, ERPNext, OpenEMR, Discourse, Mailu) and includes a synthesis pipeline for generating training trajectories and a supervised fine-tuning setup for decision-token models.

## Setup

```bash
pip install -r requirements.txt
```

Each service has its own `.env.<service>.generated` file. The active backend is selected via `PIPELINE_ENV`.

## Running Tasks

```bash
# List available services, tasks, tools
python -m safety_pipeline --list-services
python -m safety_pipeline --list-service-tasks gitea
python -m safety_pipeline --list-service-tools gitea

# Run a task from YAML
PIPELINE_ENV=gitea python -m safety_pipeline --task-file tasks/gitea/openclaw-read-readme.yaml

# Run free-form task
python -m safety_pipeline --env gitea --task "Read the README in the openclaw repository"

# Non-interactive (no blocking on ask_human)
PIPELINE_NONINTERACTIVE=1 python -m safety_pipeline --task-file tasks/gitea/openclaw-read-readme.yaml
```

## Synthesis (Trajectory Generation)

Two-pass process: Pass 1 generates pure execution traces, Pass 2 labels decisions.

```bash
python -m safety_pipeline.synthesis --task-file tasks/gitea/openclaw-read-readme.yaml
python -m safety_pipeline.synthesis --task-file tasks/gitea/openclaw-read-readme.yaml --out artifacts/synthetic_traces.jsonl
```

## Decision-Token SFT Training

```bash
cd askbench/sft
bash setup.sh
python check_env.py
python train_decision_tokens_trl.py --config train_trl_decision_tokens.yaml

# On Slurm:
sbatch run_decision_token_train.slurm
```

## Service Environment Setup/Reset

Each service has Docker-based setup and reset scripts:

```bash
bash scripts/setup_gitea_env.sh && bash scripts/reset_gitea_env.sh
bash scripts/setup_discourse_env.sh && bash scripts/reset_discourse_env.sh
# Pattern: scripts/setup_<service>_env.sh and scripts/reset_<service>_env.sh
```

## Architecture

### Runtime Loop (`safety_pipeline/runtime.py`)
The core orchestration loop. Calls the LLM with the current state, parses the decision token, and routes to: tool execution, human clarification, refusal, or replanning. Each iteration updates the trace in `state.py`.

### Decision Tokens (`safety_pipeline/decision_tokens.py`)
Four special tokens used during SFT: `<|direct_execute|>`, `<|ask_human|>`, `<|refuse|>`, `<|replan|>`. Each is followed by a branch-specific JSON payload.

### Service Backends
- `safety_pipeline/environment.py` — backend factory + Gitea implementation
- `safety_pipeline/services/backends/*.py` — per-service backend implementations
- `safety_pipeline/services/tools/*.py` — per-service tool implementations
- `safety_pipeline/backend_abc.py` — abstract interface all backends implement

### Task YAML Format (`tasks/<service>/*.yaml`)
Each task file contains the task description plus an optional `oracle` block. The current synthesis pipeline only consumes `oracle.required_tools`.

### Synthesis Pipeline (`safety_pipeline/synthesis/`)
- `pass1_runner.py` — generates pure execution traces (no decision labels)
- `pass2_reviewer.py` — labels each step with a decision token
- `trajectory_writer.py` — splices Pass 1 + Pass 2 into training data

### Key Config Files
- `safety_pipeline/settings.py` — runtime constants, env file loading
- `safety_pipeline/service_registry.py` — service metadata for all 9 backends
- `safety_pipeline/task_catalog.py` — discovers and indexes task YAML files
- `safety_pipeline/llm.py` — OpenAI-compatible client (routes through OpenRouter)
