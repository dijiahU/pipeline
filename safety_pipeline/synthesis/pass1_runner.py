import copy

from .. import runtime
from ..llm import call_auto_tool_choice
from ..settings import MAX_AGENT_TOOL_ROUNDS, set_pipeline_env
from ..state import init_conversation_state


PASS1_SYSTEM_PROMPT = """You are a strong task-completion agent.

Only real tools are available in this run. No flow tools exist.
Use the provided real tools directly to complete the task. If the task is complete, reply with text instead of calling a tool.
Use only tools from the current environment. Fill schema-required arguments explicitly."""


def _ensure_state(task_config, initial_state=None):
    if task_config.get("environment"):
        set_pipeline_env(task_config["environment"])

    if initial_state is None:
        state = init_conversation_state(task_config["task"], task_config=task_config)
    else:
        state = copy.deepcopy(initial_state)

    state.setdefault("results", [])
    state.setdefault("turn_count", 1)
    state.setdefault("last_tool_error", "")
    return state


def _build_pass1_snapshot(state):
    snapshot = {
        "user_task": state.get("initial_user_input", ""),
        "service_context": runtime.build_runtime_service_context(),
        "results": runtime._summarize_recent_results_for_snapshot(state.get("results") or []),
    }
    if state.get("last_tool_error"):
        snapshot["last_tool_error"] = state["last_tool_error"]
    return snapshot


def _build_tool_call_message(validated_step):
    return {
        "role": "assistant",
        "tool_call": {
            "tool": validated_step["tool"],
            "tool_args": validated_step["args"],
            "description": validated_step["description"],
        },
    }


def _build_tool_observation_message(tool_name, observation):
    return {
        "role": "tool",
        "tool": tool_name,
        "observation": observation,
    }


def _build_real_tool_schemas(state):
    required_real_tools, _ = runtime.build_required_real_tool_schemas(state)
    visible_real_tools = runtime.merge_real_tool_schemas(
        required_real_tools,
        runtime.get_tool_schemas(allow_empty=True),
    )
    return visible_real_tools or list(runtime.get_tool_schemas(allow_empty=True))


def _validate_real_tool_call(task_text, tool_name, tool_args):
    return runtime.validate_real_tool_step(
        {
            "tool": tool_name,
            "args": tool_args,
            "description": f"Execute {tool_name}.",
        },
        context_label="pass1.step",
        fallback_text=task_text,
    )


def _execute_validated_step(state, validated_step, transcript):
    tool_name = validated_step["tool"]
    tool_args = validated_step["args"]
    observation = runtime.execute_real_tool(tool_name, tool_args)
    transcript.append(_build_tool_call_message(validated_step))
    transcript.append(_build_tool_observation_message(tool_name, observation))
    runtime.update_state_from_execution(state, tool_name, tool_args, observation, "pass1_execute")
    return observation


def pass1_steps(pass1_trace):
    transcript = list((pass1_trace or {}).get("transcript") or [])
    extracted = []
    pending_call = None

    for item in transcript:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role == "assistant" and isinstance(item.get("tool_call"), dict):
            pending_call = item["tool_call"]
            continue
        if role == "tool" and pending_call:
            extracted.append(
                {
                    "step_index": len(extracted),
                    "tool": pending_call.get("tool", ""),
                    "tool_args": pending_call.get("tool_args") or {},
                    "description": pending_call.get("description", ""),
                    "observation": item.get("observation"),
                }
            )
            pending_call = None

    return extracted


def replay_state_before_step(task_config, pass1_trace, step_index):
    state = _ensure_state(task_config)
    for step in pass1_steps(pass1_trace)[:step_index]:
        runtime.update_state_from_execution(
            state,
            step.get("tool", ""),
            step.get("tool_args") or {},
            step.get("observation"),
            "pass1_execute",
        )
    return state


def run_task_pure(task_config, initial_state=None, injected_first_step=None):
    state = _ensure_state(task_config, initial_state=initial_state)
    transcript = [{"role": "user", "content": task_config["task"]}]
    final_response = ""
    final_status = "running"

    if injected_first_step:
        validated_injected = runtime.validate_real_tool_step(
            injected_first_step,
            context_label="injected_first_step",
            fallback_text=task_config["task"],
        )
        _execute_validated_step(state, validated_injected, transcript)

    for _ in range(MAX_AGENT_TOOL_ROUNDS):
        available_tools = _build_real_tool_schemas(state)
        if not available_tools:
            final_status = "no_available_tools"
            break

        tool_call, text_reply = call_auto_tool_choice(
            PASS1_SYSTEM_PROMPT,
            _build_pass1_snapshot(state),
            available_tools,
        )
        if tool_call is None:
            final_response = str(text_reply or "").strip()
            final_status = "done"
            break

        raw_tool_name = getattr(tool_call.function, "name", "")
        raw_tool_args = getattr(tool_call.function, "arguments", "{}")
        try:
            tool_name = runtime.resolve_real_tool_name(raw_tool_name, context_label="pass1.step")
            tool_args = runtime._parse_tool_call_arguments(tool_name, raw_tool_args)
            validated_step = _validate_real_tool_call(task_config["task"], tool_name, tool_args)
        except Exception as exc:
            state["last_tool_error"] = str(exc)
            continue

        state["last_tool_error"] = ""
        _execute_validated_step(state, validated_step, transcript)
    else:
        final_status = "max_tool_rounds_exceeded"

    if final_status == "running":
        final_status = "stopped"

    return {
        "task": task_config["task"],
        "environment": task_config.get("environment", ""),
        "transcript": transcript,
        "final_status": final_status,
        "final_response": final_response,
    }
