"""Convert GPT-5.4 traces to LLaMA-Factory ShareGPT format for Qwen SFT."""

import json
import os

from config import RESULTS_DIR, SFT_OUTPUT_PATH, TRACES_PATH
from prompts import get_prompt
from schemas import build_tools_list, build_service_context, load_real_tool_schemas


def load_traces(traces_path: str | None = None) -> list[dict]:
    """Load traces from JSONL, filter to successful ones only."""
    traces_path = traces_path or TRACES_PATH
    traces = []
    with open(traces_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("success"):
                traces.append(record)
    return traces


def _tools_to_llamafactory(service: str) -> str:
    """Convert tool schemas to LLaMA-Factory format (JSON string).

    LLaMA-Factory expects: "[{name, description, parameters}, ...]" as a string.
    Our schemas are: [{type: function, function: {name, description, parameters}}, ...]
    """
    raw_tools = build_tools_list(service)
    lf_tools = []
    for t in raw_tools:
        func = t["function"]
        lf_tools.append({
            "name": func["name"],
            "description": func["description"],
            "parameters": func["parameters"],
        })
    return json.dumps(lf_tools, ensure_ascii=False)


def _validate_predict_risk_tool(trace: dict) -> bool:
    """Check that predict_risk.tool is a real tool, not a flow tool."""
    if not trace.get("trace"):
        return False
    first_turn = trace["trace"][0]
    if first_turn["tool"] == "predict_risk":
        pr_tool = first_turn["args"].get("tool", "")
        if pr_tool in ("predict_risk", "ask_human"):
            return False
    return True


def trace_to_sft_record(trace: dict) -> dict | None:
    """Convert one successful trace to LLaMA-Factory ShareGPT format.

    Output format:
    {
        "conversations": [
            {"from": "human", "value": "snapshot JSON"},
            {"from": "function_call", "value": "{\"name\": \"predict_risk\", \"arguments\": {...}}"},
            {"from": "observation", "value": "{tool_result + updated_context}"},
            {"from": "function_call", "value": "{\"name\": \"ask_human\", \"arguments\": {...}}"},
        ],
        "system": "...",
        "tools": "[{name, description, parameters}, ...]"
    }

    Position rule: human/observation at odd positions, gpt/function_call at even positions.
    """
    if not _validate_predict_risk_tool(trace):
        return None

    service = trace["service"]
    system_prompt = get_prompt("explicit_rules")
    tools_str = _tools_to_llamafactory(service)

    # Build the same snapshot the runner would build
    snapshot = {
        "user_task": trace["task_text"],
        "flow_phase": "need_risk",
        "service_context": build_service_context(service),
    }

    turns = trace["trace"]
    result_type = trace["result"]
    conversations = []

    if result_type == "asked_directly":
        # Single turn: human → function_call(ask_human)
        # Position 1 (odd): human
        conversations.append({
            "from": "human",
            "value": json.dumps(snapshot, ensure_ascii=False, indent=2),
        })
        # Position 2 (even): function_call
        ask_turn = turns[0]
        conversations.append({
            "from": "function_call",
            "value": json.dumps({
                "name": "ask_human",
                "arguments": ask_turn["args"],
            }, ensure_ascii=False),
        })

    elif result_type == "asked_after_risky":
        # Two turns: human → function_call(predict_risk) → observation → function_call(ask_human)
        pr_turn = turns[0]
        ah_turn = turns[1]

        # Position 1 (odd): human — initial snapshot
        conversations.append({
            "from": "human",
            "value": json.dumps(snapshot, ensure_ascii=False, indent=2),
        })

        # Position 2 (even): function_call — predict_risk
        conversations.append({
            "from": "function_call",
            "value": json.dumps({
                "name": "predict_risk",
                "arguments": pr_turn["args"],
            }, ensure_ascii=False),
        })

        # Position 3 (odd): observation — tool result + updated context for next turn
        observation_content = {
            "accepted": True,
            "stored_as": "current_risk_assessment",
            "next_phase": "need_risky_branch",
            "updated_context": {
                "flow_phase": "need_risky_branch",
                "current_risk_assessment": {
                    "result": "risky",
                    "reasoning": pr_turn["args"].get("reasoning", ""),
                    "tool": pr_turn["args"].get("tool", ""),
                    "description": pr_turn["args"].get("description", ""),
                },
            },
        }
        conversations.append({
            "from": "observation",
            "value": json.dumps(observation_content, ensure_ascii=False),
        })

        # Position 4 (even): function_call — ask_human
        conversations.append({
            "from": "function_call",
            "value": json.dumps({
                "name": "ask_human",
                "arguments": ah_turn["args"],
            }, ensure_ascii=False),
        })

    else:
        return None

    return {
        "conversations": conversations,
        "system": system_prompt,
        "tools": tools_str,
    }


def export_for_qwen(
    traces_path: str | None = None,
    output_path: str | None = None,
) -> int:
    """Export all successful traces to LLaMA-Factory ShareGPT JSON."""
    output_path = output_path or SFT_OUTPUT_PATH
    traces = load_traces(traces_path)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    records = []
    skipped = 0
    for trace in traces:
        record = trace_to_sft_record(trace)
        if record is None:
            skipped += 1
            continue
        records.append(record)

    # LLaMA-Factory expects a JSON array file
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    print(f"Exported {len(records)} SFT records to {output_path}")
    if skipped:
        print(f"Skipped {skipped} records (invalid predict_risk.tool or unsupported result type)")
    return len(records)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Export traces to LLaMA-Factory ShareGPT format")
    parser.add_argument("--traces", default=None, help="Input traces JSONL path")
    parser.add_argument("--output", default=None, help="Output SFT JSON path")
    args = parser.parse_args()

    export_for_qwen(args.traces, args.output)


if __name__ == "__main__":
    main()
