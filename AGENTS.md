# Repository Guidelines

## Project Structure & Module Organization
The repository is a small Python project centered on three root entry points:

- `pipeline.py`: the current primary implementation. It is now a decision-driven safety pipeline that explicitly chooses among `act / try / replan / ask_human / refuse`, persists local memory under `memory/`, and automatically refreshes SFT-style `jsonl` samples after each run.
- `pipeline_langchain.py`: the LangChain/LangGraph version. It has not yet been fully aligned with the latest `pipeline.py` architecture and should be treated as the secondary implementation.
- `mcp_tools.py`: shared tool registry and MCP server entry point.

Supporting docs also live at the repo root:

- `README.md`: high-level project overview.
- `decision-framework.md`: current framework description.
- `期望example.md`: expected five-action examples.

Generated local artifacts live under `memory/`:

- `memory/experience_memory.json`
- `memory/tool_memory.json`
- `memory/sft_dataset.jsonl`

There is no dedicated `tests/` directory yet.

## Build, Test, and Development Commands
- `pip install -r requirements.txt`: install runtime dependencies.
- `python pipeline.py`: run the default sample task through the decision-driven pipeline.
- `python pipeline.py --task "..."`: run a single custom task and automatically persist memory plus SFT samples.
- `python pipeline_langchain.py`: run the LangGraph implementation locally.
- `python mcp_tools.py`: start the tool registry as an MCP server.
- `python -m py_compile pipeline.py pipeline_langchain.py mcp_tools.py`: quick syntax check before opening a PR.

Set `E2B_API_KEY` and `OPENAI_API_KEY` in your shell before running the actual pipeline flow. Pure local inspection of `memory/` files does not require those services to be reachable.

## Coding Style & Naming Conventions
Follow the Python style already used in the repo:

- 4-space indentation, `snake_case` for functions and variables, `PascalCase` for classes.
- Keep modules flat at the repository root unless a real package boundary emerges.
- Preserve the current style of short docstrings and explicit section dividers for major phases.
- Prefer small, composable functions over large hidden control-flow blocks.

No formatter or linter is configured today, so keep changes PEP 8-aligned and consistent with surrounding code.

## Current Pipeline Expectations
When changing `pipeline.py`, preserve the current architecture unless the task explicitly asks to change it:

1. `generate_plan`
2. `detect_step_risk`
3. `decide_action`
4. route to one of `act / try / replan / ask_human / refuse`
5. persist local artifacts

Important current behaviors:

- `think` is for fact-level analysis.
- `reflect` is for action-level inclination.
- `ExperienceMemory` stores step-level decisions and outcomes.
- `ToolMemory` stores exact-signature safe cases.
- `pipeline.py` automatically refreshes `memory/sft_dataset.jsonl` at the end of each run.

Do not describe the current system as a plan-level `safe/unsafe` blocker. That is no longer accurate.

## Testing Guidelines
There is no automated test suite yet. For every behavioral change:

- run `python -m py_compile ...` on edited files;
- execute the relevant entry point (`pipeline.py`, `pipeline_langchain.py`, or `mcp_tools.py`) when dependencies are available;
- document the manual scenario you validated, especially for decision routing, memory persistence, and SFT sample generation.

If you add tests, place them under a new `tests/` directory and name files `test_*.py`.

## Commit & Pull Request Guidelines
Recent history uses short Conventional Commit prefixes such as `feat:`, `fix:`, and `docs:`. Prefer short, imperative messages, for example `feat: export sft samples from pipeline memory`.

PRs should include:

- a clear description of the behavior change;
- the motivation for the change;
- manual validation steps and observed output;
- terminal excerpts when prompts, routing, or saved artifacts change.

## Security & Configuration Tips
Never hardcode API keys. Use environment variables only.

Treat these areas as high risk and review them carefully:

- sandbox execution behavior
- file deletion and shell execution
- outbound HTTP behavior
- automatic memory persistence
- exported SFT sample quality
