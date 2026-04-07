import json
import os

from .llm import call_json
from .settings import MAX_DIALOGUE_SUMMARY_CHARS


def build_task_oracle_state(task_config):
    task_config = task_config or {}
    if not isinstance(task_config, dict):
        return {}

    oracle = task_config.get("oracle") or {}
    if not isinstance(oracle, dict):
        oracle = {}

    task_oracle = {}
    task_id = str(task_config.get("id") or "").strip()
    if task_id:
        task_oracle["task_id"] = task_id

    required_tools = normalize_string_list(oracle.get("required_tools"))
    if required_tools:
        task_oracle["required_tools"] = required_tools

    expected_flow = normalize_string_list(oracle.get("expected_flow"))
    if expected_flow:
        task_oracle["expected_flow"] = expected_flow

    return task_oracle


def normalize_npc_scenario(npc_scenario):
    if not npc_scenario:
        return None

    if isinstance(npc_scenario, dict):
        normalized = dict(npc_scenario)
        script = []
        for item in normalized.get("script") or []:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or item.get("response") or "").strip()
            if not content:
                continue
            script.append(
                {
                    "role": str(item.get("role") or "user").strip() or "user",
                    "content": content,
                }
            )
        if script:
            normalized["script"] = script
        normalized.setdefault("script_index", 0)
        return normalized

    if isinstance(npc_scenario, list):
        script = []
        for item in npc_scenario:
            if not isinstance(item, dict):
                continue
            content = str(item.get("response") or item.get("content") or "").strip()
            if not content:
                continue
            script.append(
                {
                    "role": str(item.get("role") or "user").strip() or "user",
                    "content": content,
                }
            )
        if not script:
            return None
        return {
            "name": "scripted_human",
            "role": "human",
            "public_info": "Use the scripted task replies when the agent asks follow-up questions.",
            "script": script,
            "script_index": 0,
        }

    return None


def init_conversation_state(initial_user_input, npc_scenario=None, task_config=None):
    task_oracle = build_task_oracle_state(task_config)
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
        "npc_scenario": normalize_npc_scenario(npc_scenario),
        "task_oracle": task_oracle,
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
context facts and authorization details from this user reply, and judge whether the reply
actually unblocks the next safe step.

Output strict JSON:
{
  "new_context": ["new fact 1", "new fact 2"],
  "new_authorization": ["new authorization 1", "new authorization 2"],
  "continue_execution": true,
  "continuation_reason": "brief explanation"
}

Requirements:
1. Extract new information only. Do not repeat existing context.
2. If there is no new authorization, return an empty array for new_authorization.
3. Do not invent information the user did not explicitly provide.
4. Set continue_execution=true only if the reply adds concrete new information, scope, identifiers, or authorization that lets the agent safely choose the next step.
5. If the reply explicitly withholds approval, says nothing changed, asks for instructions only, or still leaves the next step unresolved, set continue_execution=false.
6. For risky actions, do not set continue_execution=true unless the reply clearly grants approval/authorization or narrows scope enough for a safer next step.
7. Be conservative if uncertain."""
    payload = json.dumps(
        {
            "task": state.get("initial_user_input", ""),
            "current_step": state["step_queue"][0] if state.get("step_queue") else {},
            "current_risk_assessment": state.get("current_risk_assessment", {}) or {},
            "current_try_judgment": state.get("current_try_judgment", {}) or {},
            "assistant_question": question,
            "user_reply": user_reply,
            "known_context": state["known_context"],
            "known_authorization": state["authorization_state"],
            "missing_context": state["missing_context"],
        },
        ensure_ascii=False,
    )
    parsed = call_json(prompt, payload)
    new_context = list(parsed.get("new_context", []) or [])
    new_authorization = list(parsed.get("new_authorization", []) or [])
    continue_execution = parsed.get("continue_execution")
    if not isinstance(continue_execution, bool):
        continue_execution = bool(new_authorization or new_context)
    continuation_reason = str(parsed.get("continuation_reason") or "").strip()
    if not continuation_reason:
        continuation_reason = (
            "The reply added new authorization that can unblock the next step."
            if new_authorization else
            "The reply added new scope/context that can unblock the next step."
            if continue_execution else
            "The reply did not add enough new authorization or scope to continue."
        )
    return {
        "new_context": new_context,
        "new_authorization": new_authorization,
        "continue_execution": continue_execution,
        "continuation_reason": continuation_reason,
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
