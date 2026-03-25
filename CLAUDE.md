# CLAUDE.md

## Project Overview

This repository is now **Gitea-only**. The runtime is a decision-driven, step-level safety pipeline that routes each step through `predict_risk`, `memory_for_tool`, `tool_try`, `judge_try_result`, `replan`, `ask_human`, `refuse`, or `terminate`.

## Commands

```bash
pip install -r requirements.txt
bash scripts/setup_env.sh
bash scripts/reset_env.sh
python -m safety_pipeline --task-file tasks/openclaw-read-readme.yaml
python -m safety_pipeline.evaluation --task-file tasks/openclaw-close-all-issues.yaml
python -m py_compile safety_pipeline/*.py
```

## Key Files

- `safety_pipeline/runtime.py`: orchestration state machine
- `safety_pipeline/gitea_tools.py`: Gitea tool registry and API mapping
- `safety_pipeline/environment.py`: backend abstraction and `GiteaBackend`
- `safety_pipeline/evaluation.py`: task evaluation
- `docker-compose.yml`: local Gitea service
- `scripts/setup_env.sh` / `scripts/reset_env.sh`: environment lifecycle

## Notes

- `PIPELINE_ENV` is fixed to `gitea`.
- `memory_for_plan` degrades gracefully when `faiss-cpu` is unavailable.
- `tasks/*.yaml` are the primary evaluation scenarios.
