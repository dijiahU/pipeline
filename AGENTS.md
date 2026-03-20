# Repository Guidelines

## Project Structure & Module Organization
This repository is a small Python project organized at the repo root. The main runtime lives in `pipeline.py`, which implements the decision-driven, step-level safety pipeline. `mcp_tools.py` contains the tool registry and MCP server entry point. Supporting notes live in `README.md`, `criterion.md`, and `branches.md`. Generated artifacts are stored under `memory/`, including `experience_memory.json`, `tool_memory.json`, `plan_memory_index.json`, and `sft_dataset.jsonl`. There is no `tests/` directory yet; add one only when introducing automated coverage.

## Build, Test, and Development Commands
Install dependencies with `pip install -r requirements.txt`. Run the default sample flow with `python pipeline.py`, or execute a custom task with `python pipeline.py --task "..."`. Start the MCP tool server with `python mcp_tools.py`. Before submitting changes, run `python -m py_compile pipeline.py mcp_tools.py` for a fast syntax check. When dependencies and API keys are configured, validate the relevant runtime path you changed.

## Coding Style & Naming Conventions
Use 4-space indentation and keep code PEP 8-aligned. Prefer `snake_case` for functions and variables, and `PascalCase` for classes. Match the existing style: short docstrings, explicit section dividers, and small composable helpers. No formatter or linter is configured, so keep edits tidy and consistent by hand. Keep modules at the repo root unless a real package boundary emerges.

## Testing Guidelines
There is no automated test suite today. For each behavioral change, run `python -m py_compile` on edited files and manually exercise the affected entry point. Document the scenario you validated, especially for routing decisions, sandbox `try` behavior, memory persistence, and SFT export. If you add tests, place them in `tests/` and use `test_*.py` filenames.

Current export conventions matter: `memory_for_plan` now recalls by user-task similarity, not step-only similarity; `experience_memory.json` stores compact `risk` objects (`level`, `reason`, `next_action`, `criteria`); exported SFT samples use `tool_groups.shared_flow_tools` plus `tool_groups.task_tools`. For multi-turn `ask_human`, export the follow-up as the next `human` turn instead of duplicating it in `observation`. When `completion_check.status=done`, the final user-facing reply should be exported as a trailing `gpt` message.

## Commit & Pull Request Guidelines
Recent history follows short Conventional Commit prefixes such as `feat:` and `docs:`. Use concise, imperative subjects like `feat: export plan memory index`. Pull requests should explain the behavior change, why it was needed, how it was validated, and include sample terminal output when prompts, routing, or generated artifacts change.

## Security & Architecture Notes
Do not hardcode secrets; set `OPENAI_API_KEY` and `E2B_API_KEY` in the shell. When editing `pipeline.py`, preserve the current step architecture: generate a step, assess risk, choose one of `act`, `try`, `replan`, `ask_human`, or `refuse`, then persist artifacts safely.
