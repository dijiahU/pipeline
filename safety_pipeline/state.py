import json
import os

from .llm import call_json
from .settings import MAX_DIALOGUE_SUMMARY_CHARS


def init_conversation_state(initial_user_input, npc_scenario=None):
    return {
        "initial_user_input": initial_user_input,
        "dialogue_history": [{"role": "user", "content": initial_user_input}],
        "known_context": [],
        "missing_context": [],
        "authorization_state": [],
        "results": [],
        "decision_trace": [],
        "current_flow_tool_calls": [],
        "step_queue": [],
        "current_plan_memory": None,
        "current_risk_assessment": None,
        "current_tool_memory": None,
        "current_try_result": None,
        "current_try_exec_result": None,
        "current_try_judgment": None,
        "final_reply": "",
        "tool_call_counter": 0,
        "flow_phase": "need_step",
        "pending_execution_method": "",
        "current_step_recorded": False,
        "replan_counts": {},
        "status": "running",
        "turn_count": 1,
        "error_reason": "",
        "last_tool_error": "",
        "npc_scenario": npc_scenario,
    }


def extend_unique(items, new_items):
    for item in new_items:
        if item and item not in items:
            items.append(item)


def truncate_text(text, limit=MAX_DIALOGUE_SUMMARY_CHARS):
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def summarize_execution_result(tool_name, args, result):
    summary = f"{tool_name}({json.dumps(args, ensure_ascii=False, sort_keys=True)}) -> {result}"
    return truncate_text(summary)


def append_assistant_message(state, content):
    state["dialogue_history"].append({"role": "assistant", "content": content})


def reset_step_artifacts(state):
    state["current_plan_memory"] = None
    state["current_risk_assessment"] = None
    state["current_tool_memory"] = None
    state["current_try_result"] = None
    state["current_try_exec_result"] = None
    state["current_try_judgment"] = None
    state["pending_execution_method"] = ""
    state["current_step_recorded"] = False


def get_current_step(state):
    if state["step_queue"]:
        return state["step_queue"][0]
    return None


def clear_current_flow_tool_calls(state):
    state["current_flow_tool_calls"] = []


def update_latest_flow_tool_arguments(state, arguments):
    if not state.get("current_flow_tool_calls"):
        return
    state["current_flow_tool_calls"][-1]["arguments"] = arguments


def summarize_trace_value(value):
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [summarize_trace_value(item) for item in value[:3]]
    if isinstance(value, dict):
        summary = {}
        for key in list(value.keys())[:8]:
            summary[key] = summarize_trace_value(value[key])
        return summary
    return str(value)


def build_flow_tool_call_record(call_index, phase, tool_name, arguments, result):
    record = {
        "call_index": call_index,
        "phase": phase,
        "tool_name": tool_name,
        "arguments": arguments,
        "result": summarize_trace_value(result),
    }
    return record


def summarize_result_for_memory(value, limit=220):
    summarized = summarize_trace_value(value)
    if isinstance(summarized, str):
        text = summarized
    else:
        text = json.dumps(summarized, ensure_ascii=False)
    return truncate_text(text, limit)


def normalize_string_list(value):
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def normalize_risk_assessment_payload(payload):
    payload = payload or {}
    if not isinstance(payload, dict):
        return {}
    result = str(payload.get("result") or payload.get("level") or payload.get("risk") or "").strip()
    reasoning = str(payload.get("reasoning") or payload.get("reason") or "").strip()

    normalized = {}
    if result:
        normalized["result"] = result
    if reasoning:
        normalized["reasoning"] = reasoning
    return normalized


def compact_risk_record(risk_assessment):
    normalized = normalize_risk_assessment_payload(risk_assessment)
    if not normalized:
        return {}
    return {
        "level": normalized.get("result", ""),
        "reason": normalized.get("reasoning", ""),
    }


def get_case_risk_assessment(case):
    case = case or {}
    normalized = normalize_risk_assessment_payload(case.get("risk"))
    if normalized:
        return normalized
    return normalize_risk_assessment_payload(case.get("risk_assessment"))


def update_state_from_execution(state, tool_name, args, result, method):
    summary = summarize_execution_result(tool_name, args, result)
    state["results"].append({"tool": tool_name, "args": args, "result": result, "method": method})
    extend_unique(state["known_context"], [summary])
    append_assistant_message(state, f"[{method}] {summary}")


def build_memory_context_snapshot(state):
    return {
        "dialogue_history": list(state["dialogue_history"]),
        "known_context": [truncate_text(c, 120) for c in state["known_context"]],
        "missing_context": list(state["missing_context"]),
        "authorization_state": list(state["authorization_state"]),
        "results_summary": [
            truncate_text(
                summarize_execution_result(item["tool"], item.get("args", {}), item["result"]),
                120,
            )
            for item in state["results"]
        ],
    }


def parse_user_reply_to_state_update(state, question, user_reply):
    prompt = """You are a conversation state parsing assistant. Extract any newly provided
context facts and authorization details from this user reply.

Output strict JSON:
{
  "new_context": ["new fact 1", "new fact 2"],
  "new_authorization": ["new authorization 1", "new authorization 2"]
}

Requirements:
1. Extract new information only. Do not repeat existing context.
2. If there is no new authorization, return an empty array for new_authorization.
3. Do not invent information the user did not explicitly provide."""
    payload = json.dumps(
        {
            "assistant_question": question,
            "user_reply": user_reply,
            "known_context": state["known_context"],
            "known_authorization": state["authorization_state"],
            "missing_context": state["missing_context"],
        },
        ensure_ascii=False,
    )
    parsed = call_json(prompt, payload)
    return {
        "new_context": list(parsed.get("new_context", []) or []),
        "new_authorization": list(parsed.get("new_authorization", []) or []),
    }


def apply_user_reply_to_state(state, question, user_reply):
    state["dialogue_history"].append({"role": "user", "content": user_reply})
    state["turn_count"] += 1
    state_update = parse_user_reply_to_state_update(state, question, user_reply)
    extend_unique(state["known_context"], state_update["new_context"])
    extend_unique(state["authorization_state"], state_update["new_authorization"])
    state["missing_context"] = []
    state["step_queue"] = []
    return state_update


def request_user_input_for_state(state, question, missing_context=None):
    append_assistant_message(state, question)
    print(f"\n[HUMAN] Question: {question}")
    if os.environ.get("PIPELINE_NONINTERACTIVE") == "1":
        state["status"] = "aborted"
        return {
            "status": "aborted",
            "human_reply": "",
            "state_update": {},
            "error": "non_interactive_ask_human",
        }
    human_reply = input("[HUMAN] Reply (enter 'abort' to stop): ").strip()
    if human_reply.lower() == "abort":
        state["status"] = "aborted"
        return {"status": "aborted", "human_reply": human_reply}

    if missing_context:
        state["missing_context"] = list(missing_context)
    state_update = apply_user_reply_to_state(state, question, human_reply)
    return {
        "status": "updated",
        "human_reply": human_reply,
        "state_update": state_update,
    }
