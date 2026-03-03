# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Agent Safety Pipeline — an LLM-based safety execution framework built on E2B cloud sandboxes. It implements a **Plan + Execution dual-verification architecture** to safely execute agent tool calls while preventing data exfiltration, file deletion, and privilege escalation.

## Commands

```bash
pip3 install -r requirements.txt          # Install dependencies
python3 pipeline.py                       # Run native OpenAI version
python3 pipeline_langchain.py             # Run LangChain/LangGraph version
python3 mcp_tools.py                      # Run as standalone MCP Server
```

Required environment variables: `E2B_API_KEY`, `OPENAI_API_KEY`

## Architecture

### Two-Stage Dual Verification

1. **Plan Phase** — LLM generates execution plan, performs risk assessment (`predict: safe/unsafe`), replans up to 3 times if risky, escalates to human if vague or exceeds max replans. Uses cognitive flow tags: `<think>` → `<memory_call>` → `<predict>` → `<reflect>`.

2. **Execution Phase** — Checks tool memory for previously verified safe calls. For new calls: executes in E2B sandbox with file state snapshots (md5sum), LLM compares pre/post state to judge safety. Safe results are cached in memory; unsafe results are blocked with human escalation.

### Key Files

- **pipeline.py** — Native OpenAI API implementation with manual JSON parsing and explicit control flow. Core functions: `plan_phase()`, `generate_plan()`, `detect_risk()`, `execution_phase()`, `tool_try_in_sandbox()`, `judge_safety()`.
- **pipeline_langchain.py** — LangChain/LangGraph version using `with_structured_output()` (Pydantic models) and `StateGraph` for declarative flow control. Models: `Plan`, `PlanStep`, `RiskAssessment`, `SafetyJudgment`.
- **mcp_tools.py** — Decorator-based tool registry (`@tool(name, description, params)`). Dynamic discovery via `get_all_schemas()` and `call_tool(name, args)`. Adding new tools requires only decorating a function — no pipeline modification needed.

### Memory System

- **PlanMemory** — Stores risky plans to inform future LLM reasoning.
- **ToolMemory** — Caches verified safe tool calls keyed by signature (`tool_name:json(args)`).

### pipeline.py vs pipeline_langchain.py

| Aspect | pipeline.py | pipeline_langchain.py |
|--------|-------------|----------------------|
| LLM Calls | `openai.ChatCompletion` | `ChatOpenAI` |
| Output Parsing | Manual `json.loads()` | `with_structured_output()` |
| Flow Control | if/else + loops | LangGraph StateGraph |

Both implement the same dual-verification logic; the LangGraph version is more declarative.
