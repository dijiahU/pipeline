# Repository Guidelines

## Project Structure & Module Organization
This repo is a small Python project with flat root-level modules:

- `pipeline.py`: primary decision-driven safety pipeline. It routes each step through `act`, `try`, `replan`, `ask_human`, or `refuse`, then persists local memory artifacts.
- `mcp_tools.py`: shared tool registry and MCP server entry point.
- `README.md`, `decision-framework.md`, `期望example.md`: project and behavior notes.

Generated artifacts live under `memory/`:
`experience_memory.json`, `tool_memory.json`, and `sft_dataset.jsonl`.
There is no `tests/` directory yet; add one only when introducing automated tests.

## Build, Test, and Development Commands
- `pip install -r requirements.txt`: install runtime dependencies.
- `python pipeline.py`: run the default sample flow.
- `python pipeline.py --task "..."`: run one custom task and refresh memory plus SFT samples.
- `python mcp_tools.py`: start the MCP tool server.
- `python -m py_compile pipeline.py mcp_tools.py`: quick syntax check before a PR.

Set `OPENAI_API_KEY` and `E2B_API_KEY` in your shell before running live flows.

## Coding Style & Naming Conventions
Use 4-space indentation, `snake_case` for functions and variables, and `PascalCase` for classes. Keep modules at the repo root unless a real package boundary emerges. Match the existing style: short docstrings, explicit section dividers, and small composable functions. No formatter or linter is configured, so keep edits PEP 8-aligned.

When editing `pipeline.py`, preserve the current step architecture: `generate_plan`, `detect_step_risk`, `decide_action`, route to one of the five actions, then persist artifacts.

## Testing Guidelines
There is no automated suite yet. For every behavioral change, run `python -m py_compile ...` on edited Python files and execute the relevant entry point when dependencies are available. Document the manual scenario you validated, especially decision routing, memory persistence, and SFT sample generation. If you add tests, place them in `tests/` and name files `test_*.py`.

## Commit & Pull Request Guidelines
Recent history uses short Conventional Commit prefixes such as `feat:` and `fix:`. Prefer concise imperative messages like `feat: export sft samples from memory`.

PRs should include the behavior change, motivation, manual validation steps, and sample terminal output when prompts, routing, or saved artifacts change.

## Security & Configuration Tips
Never hardcode API keys. Review sandbox execution, file deletion, outbound HTTP calls, automatic memory persistence, and exported SFT data carefully; these are the highest-risk areas in this repo.
