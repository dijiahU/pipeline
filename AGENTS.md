# Repository Guidelines

## Project Structure & Module Organization
Primary code lives in `safety_pipeline/`. `runtime.py` orchestrates the step-level safety loop, `gitea_tools.py` registers Gitea actions, `environment.py` defines the backend abstraction, and `evaluation.py` runs task-level checks. Supporting modules such as `memory.py`, `state.py`, `llm.py`, and `settings.py` hold persistence, session state, model access, and config. Scenario definitions live in `tasks/*.yaml`. Gitea environment assets are in `docker/` and `scripts/`. Generated artifacts belong under `memory/`.

## Build, Test, and Development Commands
Install dependencies with `pip install -r requirements.txt`.

Start and initialize the local Gitea environment with `bash scripts/setup_env.sh`.

Reset the environment and reseed repositories with `bash scripts/reset_env.sh`.

Run a task with `python -m safety_pipeline --task-file tasks/openclaw-read-readme.yaml`.

Run the evaluator with `python -m safety_pipeline.evaluation --task-file tasks/openclaw-change-branch-policy.yaml`.

Do a fast syntax pass with `python -m py_compile safety_pipeline/*.py`.

## Coding Style & Naming Conventions
Use 4-space indentation and keep code PEP 8-aligned. Prefer `snake_case` for functions and variables, `PascalCase` for classes, and small composable helpers over large functions. Match the existing style: short docstrings, explicit section dividers, and direct control flow. No formatter or linter is configured, so keep edits consistent by hand.

## Testing Guidelines
This repo relies mainly on scenario-based validation. After each change, run `python -m py_compile` on edited files and manually exercise the affected runtime path. For behavior changes, validate the relevant YAML task or evaluation command and note what you checked. If you add automated tests, place them in `tests/` and use `test_*.py`.

## Commit & Pull Request Guidelines
Follow the existing short Conventional Commit style, for example `feat: add gitea backend` or `docs: update runtime guide`. Pull requests should explain the behavior change, why it was needed, how it was validated, and include sample terminal output when prompts, routing, or generated artifacts change.

## Security & Architecture Notes
Do not hardcode secrets. Set `OPENAI_API_KEY`, and use `GITEA_BASE_URL`, `GITEA_ACCESS_TOKEN`, and `GITEA_OWNER` for the local backend. When editing `safety_pipeline/runtime.py`, preserve the current step architecture: generate one step, assess risk, route through `memory_for_tool` or `tool_try`, commit safe try results with `try_commit`, and only allow `unsafe` tries to continue via `ask_human` or `terminate`.
