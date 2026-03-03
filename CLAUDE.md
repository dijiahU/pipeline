# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Agent Safety Pipeline — an LLM-based safety execution framework built on E2B cloud sandboxes. It implements a **Plan + Execution dual-verification architecture** to safely execute agent tool calls while preventing data exfiltration, file deletion, and privilege escalation.

## Commands

```bash
pip install -r requirements.txt            # Install dependencies
python pipeline.py                         # Run native OpenAI version
python pipeline_langchain.py               # Run LangChain/LangGraph version
python mcp_tools.py                        # Run as standalone MCP Server
```

Required environment variables: `E2B_API_KEY`, `OPENAI_API_KEY` (read via `os.environ.get()`)

## Architecture

### Two-Stage Dual Verification

1. **Plan Phase** — LLM generates execution plan, performs risk assessment (`predict: safe/unsafe`), replans up to 3 times if risky, escalates to human if vague or exceeds max replans. Uses cognitive flow tags: `<think>` → `<memory_call>` → `<predict>` → `<reflect>`.

2. **Execution Phase** — Checks tool memory for previously verified safe calls (exact signature match). For new calls: executes in E2B sandbox with file state snapshots (md5sum before/after), LLM compares pre/post state to judge safety. Safe results are cached in memory; unsafe results are blocked with human escalation.

### Key Files

- **pipeline.py** — Native OpenAI API implementation with `openai.OpenAI` client, `response_format=json_object`, manual `json.loads()` parsing, and `while True` loop for human feedback. Core functions: `plan_phase()`, `generate_plan()`, `detect_risk()`, `execution_phase()`, `tool_try_in_sandbox()`, `judge_safety()`.
- **pipeline_langchain.py** — LangChain/LangGraph version using `ChatOpenAI` + `with_structured_output(Pydantic, method="function_calling")` and `StateGraph` for declarative flow control. Pydantic models: `Plan`, `PlanStep`, `RiskAssessment`, `SafetyJudgment`. 6 graph nodes + 4 routing functions.
- **mcp_tools.py** — Decorator-based tool registry (`@tool(name, description, params)`). Auto-extracts `required` params from function signatures. Dynamic discovery via `get_all_schemas()` and `call_tool(name, args)`. Can run as standalone MCP Server via `FastMCP`.

### Memory System

- **PlanMemory** — Stores risky plans (`{task, plan, risk_reason}`) to inform future LLM reasoning. Recalled by dumping entire list as JSON into system prompt (no semantic retrieval).
- **ToolMemory** — Caches verified safe tool calls keyed by exact signature (`tool_name:json(args)`). On hit, skips sandbox and executes directly.

**Key difference**: In `pipeline.py`, both memories are global objects (persist across `pipeline()` calls within a process). In `pipeline_langchain.py`, they live in `PipelineState` and reset each `pipeline()` call. Neither version persists memory to disk.

### pipeline.py vs pipeline_langchain.py

| Aspect | pipeline.py | pipeline_langchain.py |
|--------|-------------|----------------------|
| LLM Calls | `openai.OpenAI` + `json_object` format | `ChatOpenAI` + `with_structured_output()` |
| Output Parsing | Manual `json.loads()` | Pydantic models (auto) |
| Flow Control | `while True` + if/else | LangGraph `StateGraph` + conditional edges |
| Memory Scope | Global (cross-call) | Per-invocation (state-based) |

Both implement the same dual-verification logic; the LangGraph version is more declarative.
