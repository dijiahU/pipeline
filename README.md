# Agent Safety Pipeline

A decision-driven safety pipeline for multi-service admin tasks.

The current runtime is built around one safety gate:

`predict_risk -> direct_execute / ask_human / refuse / replan`

The old speculative execution path is gone. There is no checkpoint rollback workflow, no tool-memory branch, and no scenario-specific memory retrieval in the runtime.

## What This Repo Contains

- `safety_pipeline/`
  - Main runtime, service backends, tool registry, evaluation, and synthesis
- `tasks/<service>/*.yaml`
  - Task definitions and oracles
- `scripts/`
  - Local service setup/reset helpers
- `askbench/sft/`
  - Minimal decision-token SFT training path
- `artifacts/`
  - Exported traces and training datasets

## Runtime Model

For each minimal real-tool step, the runtime asks the model to choose one branch:

- `direct_execute`
  - execute the selected real tool immediately
- `ask_human`
  - ask one concrete blocking question
- `refuse`
  - stop because the request should not be helped with
- `replan`
  - replace the current step with one safer concrete step

The runtime now adapts through task design and deployment thresholds, not through recalled memory.

## Repository Layout

- [safety_pipeline/runtime.py](/home/hcj/pipeline/safety_pipeline/runtime.py)
  - Main orchestration loop and CLI entry
- [safety_pipeline/state.py](/home/hcj/pipeline/safety_pipeline/state.py)
  - Conversation state and trace shaping
- [safety_pipeline/evaluation.py](/home/hcj/pipeline/safety_pipeline/evaluation.py)
  - Task-level checks for decision, behavior, and outcomes
- [safety_pipeline/session_store.py](/home/hcj/pipeline/safety_pipeline/session_store.py)
  - Persistent trace-session export
- [safety_pipeline/decision_tokens.py](/home/hcj/pipeline/safety_pipeline/decision_tokens.py)
  - `<|direct_execute|> / <|ask_human|> / <|refuse|> / <|replan|>` helpers
- [safety_pipeline/synthesis/](/home/hcj/pipeline/safety_pipeline/synthesis)
  - Two-pass synthetic trajectory generation
- [askbench/sft/](/home/hcj/pipeline/askbench/sft)
  - Decision-token SFT setup and Slurm training entrypoints

## Supported Services

- Gitea
- Rocket.Chat
- ownCloud
- NocoDB
- Zammad
- ERPNext
- OpenEMR
- Discourse
- Mailu

## Install

```bash
pip install -r requirements.txt
```

## Environment

The runtime auto-loads:

- `.env`
- `.env.<service>.generated`

Minimal `.env` example:

```env
OPENAI_API_KEY=your_openai_api_key
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_MODEL=openai/gpt-5.4
PIPELINE_ENV=gitea
```

`PIPELINE_ENV` selects the backend. Task YAML files can override it through their own `environment` field.

## Service Setup

For Gitea, the generic wrappers are just convenience wrappers around the local Docker-based Gitea environment:

```bash
bash scripts/setup_env.sh
bash scripts/reset_env.sh
```

They are not a universal service dispatcher.

For other services, use the service-specific scripts directly:

```bash
bash scripts/setup_discourse_env.sh
bash scripts/reset_discourse_env.sh

bash scripts/setup_erpnext_env.sh
bash scripts/reset_erpnext_env.sh

bash scripts/setup_openemr_env.sh
bash scripts/reset_openemr_env.sh
```

## Run The Pipeline

List registered services, tasks, and tools:

```bash
python -m safety_pipeline --list-services
python -m safety_pipeline --list-service-tasks gitea
python -m safety_pipeline --list-service-tools gitea
```

Run a task file:

```bash
PIPELINE_ENV=gitea python -m safety_pipeline --task-file tasks/gitea/openclaw-read-readme.yaml
PIPELINE_ENV=owncloud python -m safety_pipeline --task-file tasks/owncloud/owncloud-list-documents.yaml
```

Run a free-form task against a selected backend:

```bash
python -m safety_pipeline --env gitea --task "Read the README in the openclaw repository"
```

If you do not want `ask_human` to block on terminal input:

```bash
PIPELINE_NONINTERACTIVE=1 python -m safety_pipeline --task-file tasks/gitea/openclaw-read-readme.yaml
```

## Task Evaluation

Run the evaluator:

```bash
python -m safety_pipeline.evaluation --task-file tasks/gitea/gitea-read-issue-detail.yaml
python -m safety_pipeline.evaluation --task-file tasks/openemr/openemr-view-patient.yaml --eval-only
```

The evaluator checks:

- `tool_coverage_check`
- `decision_check`
- `outcome_check`
- `behavior_check`

## Two-Pass Synthesis

Synthetic trace generation now uses two passes:

1. Pass 1 executes a pure real-tool trajectory.
2. Pass 2 reviews each step and labels it as `direct_execute`, `ask_human`, `refuse`, or `replan`.
3. The writer splices those reviewed decisions back into pipeline-shaped session cases.

Example:

```bash
python -m safety_pipeline.synthesis --task-file tasks/gitea/openclaw-read-readme.yaml
```

You can also dump traces to a JSONL file:

```bash
python -m safety_pipeline.synthesis \
  --task-file tasks/gitea/openclaw-read-readme.yaml \
  --out artifacts/synthetic_traces.jsonl
```

## Decision-Token Training Path

This repo now includes a minimal SFT path for smaller models under [askbench/sft/](/home/hcj/pipeline/askbench/sft).

Training targets use one leading decision token followed by compact branch-specific JSON:

- `<|direct_execute|>{"tool":"...","tool_args":{...},"description":"..."}`
- `<|ask_human|>{"question":"..."}`
- `<|refuse|>{"reason":"..."}`
- `<|replan|>{"reason":"...","new_step":{"tool":"...","args":{...},"description":"..."}}`

Quick start:

```bash
cd askbench/sft
bash setup.sh
python check_env.py
```

Then update `model_name_or_path` in [train_lora_gpu_decision_tokens.yaml](/home/hcj/pipeline/askbench/sft/train_lora_gpu_decision_tokens.yaml) and train:

```bash
DISABLE_VERSION_CHECK=1 llamafactory-cli train train_lora_gpu_decision_tokens.yaml
```

On Slurm:

```bash
sbatch run_decision_token_train.slurm
```

See also:

- [askbench/sft/README.md](/home/hcj/pipeline/askbench/sft/README.md)
- [askbench/CLUSTER_USAGE.md](/home/hcj/pipeline/askbench/CLUSTER_USAGE.md)
- [askbench/ASPIRE_slurm_tutorial.md](/home/hcj/pipeline/askbench/ASPIRE_slurm_tutorial.md)

## Artifacts

Current exported artifacts are written under `artifacts/`:

- `artifacts/trace_sessions.jsonl`
  - session-level traces collected from runtime and synthesis
- `artifacts/decision_token_sft.json`
  - decision-token SFT dataset for smaller deployment models

## Notes

- Task YAML files should declare `service`, `environment`, and `oracle`.
- `scripts/setup_env.sh` and `scripts/reset_env.sh` are Gitea convenience wrappers for local Docker setup, not universal service dispatchers.
- The runtime no longer uses historical memory retrieval.
- The online runtime still follows the structured `predict_risk` workflow; the decision-token path is the training/export path for smaller models.
