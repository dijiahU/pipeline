# Repository Guidelines

## Project Structure & Module Organization
This repository is a small Python project organized around the `safety_pipeline/` package. The main runtime lives in `safety_pipeline/runtime.py`, which implements the decision-driven, step-level safety pipeline. `safety_pipeline/gitlab_tools.py` contains the GitLab API tool registry using `@gitlab_tool()` decorator and `_REGISTRY` pattern. `safety_pipeline/environment.py` defines the `EnvironmentBackend` abstraction and `GitLabBackend` implementation. `safety_pipeline/evaluation.py` provides the task-level evaluation framework with decision, outcome, and behavior checks. Task definitions live in `tasks/*.yaml`. `docker-compose.yml` and `scripts/setup_env.sh` handle the GitLab test environment. Supporting notes live in `README.md`, `criterion.md`, and `branches.md`. Generated artifacts are stored under `memory/`, including `experience_memory.json`, `tool_memory.json`, `plan_memory.faiss`, `plan_memory_meta.json`, and `sft_dataset.jsonl`. There is no `tests/` directory yet; add one only when introducing automated coverage.

## Build, Test, and Development Commands
Install dependencies with `pip install -r requirements.txt`. Run the default sample flow with `python -m safety_pipeline`, or execute a custom task with `python -m safety_pipeline --task "..."`. Run a task from YAML definition with `python -m safety_pipeline --task-file tasks/safety-list-projects.yaml`. Run the evaluator with `python -m safety_pipeline.evaluation --task-file tasks/safety-close-all-issues.yaml`. Start the GitLab environment with `docker compose up -d && bash scripts/setup_env.sh`. Before submitting changes, run `python -m py_compile safety_pipeline/*.py` for a fast syntax check. When dependencies and API keys are configured, validate the relevant runtime path you changed.

## Coding Style & Naming Conventions
Use 4-space indentation and keep code PEP 8-aligned. Prefer `snake_case` for functions and variables, and `PascalCase` for classes. Match the existing style: short docstrings, explicit section dividers, and small composable helpers. No formatter or linter is configured, so keep edits tidy and consistent by hand. Keep related modules grouped under `safety_pipeline/` rather than expanding the repo root.

## Testing Guidelines
There is no automated test suite today. For each behavioral change, run `python -m py_compile` on edited files and manually exercise the affected entry point. Document the scenario you validated, especially for routing decisions, sandbox `try` behavior, memory persistence, and SFT export. If you add tests, place them in `tests/` and use `test_*.py` filenames. Task YAML files in `tasks/` serve as the primary evaluation scenarios.

Current export conventions matter: `memory_for_plan` now recalls by user-task similarity, not step-only similarity; `experience_memory.json` stores compact `risk` objects (`level`, `reason`, `next_action`, `criteria`); exported SFT samples use `tool_groups.shared_flow_tools` plus `tool_groups.task_tools`. For multi-turn `ask_human`, export the follow-up as the next `human` turn instead of duplicating it in `observation`. When `completion_check.status=done`, the final user-facing reply should be exported as a trailing `gpt` message.

## Commit & Pull Request Guidelines
Recent history follows short Conventional Commit prefixes such as `feat:` and `docs:`. Use concise, imperative subjects like `feat: export plan memory index`. Pull requests should explain the behavior change, why it was needed, how it was validated, and include sample terminal output when prompts, routing, or generated artifacts change.

## Security & Architecture Notes
Do not hardcode secrets; set `OPENAI_API_KEY` in the shell. GitLab access uses `GITLAB_BASE_URL` (default `http://localhost:8929`) and `GITLAB_ACCESS_TOKEN` (default `root-token`). When editing `safety_pipeline/runtime.py`, preserve the current step architecture: generate a step, assess risk, choose one of `act`, `try`, `replan`, `ask_human`, or `refuse`, then persist artifacts safely. New service backends should follow the `safety_pipeline/gitlab_tools.py` + `EnvironmentBackend` pattern.

> 查看上一次修改详情：[changes.md](./changes.md)
