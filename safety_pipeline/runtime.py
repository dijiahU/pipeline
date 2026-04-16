import argparse
import difflib
import json
import os
import re

from .console import print_json_block, print_stage_end, print_stage_start
from .decision_tokens import (
    DECISION_TOKEN_SYSTEM_PROMPT,
    build_branch_target_text,
    branch_payload_from_case,
)
from .environment import get_supported_backend_names
from .exceptions import ToolExecutionError
from .llm import call_required_tool_choice
from .session_store import append_session_record, load_session_cases
from .service_registry import build_service_summary, get_service_spec
from .settings import (
    DECISION_TOKEN_SFT_PATH,
    MAX_AGENT_TOOL_ROUNDS,
    MAX_CONVERSATION_TURNS,
    MAX_STEP_REPLAN,
    MAX_TOOL_CALL_RETRIES,
    SFT_DATASET_PATH,
    SFT_STEPWISE_PATH,
    TRACE_SESSION_PATH,
    get_pipeline_env,
    set_pipeline_env,
)
from .task_catalog import build_service_task_index
from .tool_retrieval import ToolIndex
from .state import (
    append_assistant_message,
    build_dialogue_context_snapshot,
    build_flow_tool_call_record,
    clear_current_flow_tool_calls,
    compact_risk_record,
    get_current_step,
    init_conversation_state,
    normalize_string_list,
    request_user_input_for_state,
    reset_step_artifacts,
    summarize_result_text,
    summarize_trace_value,
    update_latest_flow_tool_arguments,
    update_state_from_execution,
)

try:
    import yaml
except ModuleNotFoundError:
    yaml = None


_RUNTIME_TOOL_INDEX_CACHE = {"signature": None, "index": None}


def get_environment_backend():
    """Get the current environment backend."""
    from .environment import get_backend
    return get_backend(get_pipeline_env())


def get_tool_schemas(allow_empty=False):
    try:
        return get_environment_backend().get_tool_schemas()
    except RuntimeError:
        if allow_empty:
            return []
        raise

def get_real_tool_schema_map():
    tool_map = {}
    for schema in get_tool_schemas():
        func = schema["function"]
        tool_map[func["name"]] = func
    return tool_map


def get_real_tool_schema_bundle_map(allow_empty=False):
    tool_map = {}
    for schema in get_tool_schemas(allow_empty=allow_empty):
        func = schema.get("function") or {}
        name = func.get("name", "")
        if name:
            tool_map[name] = schema
    return tool_map


def build_tool_schema_hint(tool_name):
    if not tool_name:
        return ""
    tool_schema = get_real_tool_schema_map().get(tool_name)
    if not tool_schema:
        return ""
    parameters = tool_schema.get("parameters", {})
    return (
        f"; correct parameter schema for {tool_name}: "
        f"{json.dumps(parameters, ensure_ascii=False)}"
    )


def _parse_tool_call_arguments(tool_name, raw_arguments):
    if raw_arguments in (None, ""):
        return {}
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if not isinstance(raw_arguments, str):
        raise RuntimeError(
            f"Tool call arguments for {tool_name or '[unknown]'} must be a JSON object string, "
            f"got {type(raw_arguments).__name__}."
        )
    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Tool call arguments for {tool_name or '[unknown]'} were not valid JSON: "
            f"{exc.msg} at line {exc.lineno} column {exc.colno}."
        ) from exc
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise RuntimeError(
            f"Tool call arguments for {tool_name or '[unknown]'} must decode to a JSON object, "
            f"got {type(parsed).__name__}."
        )
    return parsed


def resolve_real_tool_name(tool_name, context_label="current_step"):
    tool_name = str(tool_name).strip()
    tool_map = get_real_tool_schema_map()
    if tool_name in tool_map:
        return tool_name

    for prefix in ("functions.", "function.", "tools."):
        if tool_name.startswith(prefix):
            stripped = tool_name[len(prefix):].strip()
            if stripped in tool_map:
                return stripped

    if tool_name in FLOW_TOOL_SCHEMAS:
        raise RuntimeError(
            f"{context_label}.tool cannot use a flow tool: {tool_name}. "
            f"If you need to ask a follow-up question, refuse, replan, or make a risk judgment, call the top-level {tool_name} directly instead of putting it inside a real execution step."
        )

    aliases = {
        "delete_files": "delete_file",
        "get_ci_logs": "get_latest_pipeline_log",
        "get_ci_pipeline_logs": "get_latest_pipeline_log",
    }
    mapped = aliases.get(tool_name)
    if mapped and mapped in tool_map:
        return mapped

    candidates = difflib.get_close_matches(tool_name, list(tool_map.keys()), n=1, cutoff=0.88)
    if candidates:
        return candidates[0]

    raise RuntimeError(f"{context_label}.tool used an unknown real tool: {tool_name}")


def _parse_inline_arg_value(raw_text, schema):
    text = str(raw_text or "").lstrip()
    if not text:
        return None, 0

    decoder = json.JSONDecoder()
    if text[0] in ('{', '[', '"'):
        try:
            value, end = decoder.raw_decode(text)
            return value, end
        except Exception:
            return None, 0

    if text[0] == "'":
        end_idx = text.find("'", 1)
        if end_idx == -1:
            return None, 0
        return text[1:end_idx], end_idx + 1

    match = re.match(r"^[^\uFF0C,\uFF1B;\u3002\)\]\}\s]+", text)
    if not match:
        return None, 0
    raw_value = match.group(0)
    value_type = (schema or {}).get("type", "")

    if value_type == "integer":
        try:
            return int(raw_value), len(raw_value)
        except ValueError:
            return None, 0
    if value_type == "number":
        try:
            return float(raw_value), len(raw_value)
        except ValueError:
            return None, 0
    if value_type == "boolean":
        lowered = raw_value.lower()
        if lowered == "true":
            return True, len(raw_value)
        if lowered == "false":
            return False, len(raw_value)
        return None, 0
    return raw_value, len(raw_value)


def _extract_inline_tool_args(description, properties, existing_args=None):
    description = str(description or "")
    if not description:
        return dict(existing_args or {})

    merged = dict(existing_args or {})
    for key, schema in (properties or {}).items():
        if key in merged:
            continue

        pattern = rf"(?<![A-Za-z0-9_]){re.escape(key)}\s*=\s*"
        match = re.search(pattern, description)
        if match:
            value, consumed = _parse_inline_arg_value(description[match.end():], schema or {})
            if consumed > 0:
                merged[key] = value
                continue

        if key in {"name", "table_name"} and (schema or {}).get("type") == "string":
            named_match = re.search(
                r"(?:named|\u540d\u4e3a)\s*[\"'\u201C\u201D]?([^\"'\u201C\u201D\u3002\uff0c,\uff1b;\s]+)[\"'\u201C\u201D]?",
                description,
                flags=re.IGNORECASE,
            )
            if named_match:
                merged[key] = named_match.group(1)

    return merged


def _infer_project_id_from_text(text):
    text = str(text or "")
    if not text:
        return ""

    explicit_patterns = [
        r"(?:repository|project)\s+(?:id|path)?\s*[=:]?\s*[\"'`]?([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)[\"'`]?",
        r"[\"'`]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)[\"'`]\s+repository",
    ]
    for pattern in explicit_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)

    repo_name_patterns = [
        r"(?:the\s+)?[\"'`]?([A-Za-z0-9_.-]+)[\"'`]?\s+repository\b",
        r"\brepository\s+(?:named\s+)?[\"'`]?([A-Za-z0-9_.-]+)[\"'`]?\b",
    ]
    for pattern in repo_name_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        repo_name = match.group(1)
        if not repo_name:
            continue
        owner = str(os.environ.get("GITEA_OWNER", "root") or "root").strip()
        return f"{owner}/{repo_name}" if owner else repo_name

    return ""


def _infer_numeric_id_args_from_text(text, properties, existing_args=None):
    text = str(text or "")
    merged = dict(existing_args or {})
    if not text:
        return merged

    missing_integer_ids = []
    for key, schema in (properties or {}).items():
        if key in merged or key == "project_id" or not key.endswith("_id"):
            continue
        if (schema or {}).get("type") == "integer":
            missing_integer_ids.append(key)

    for key in missing_integer_ids:
        base = key[:-3]
        full_label = r"\s+".join(re.escape(part) for part in base.split("_") if part)
        tail_token = re.escape((base.split("_") or [""])[-1])
        patterns = [
            rf"\b{full_label}\b\s+with\s+id\s+(\d+)\b",
            rf"\b{full_label}\b\s*(?:id|#)\s*(\d+)\b",
            rf"\b\w*{tail_token}\w*\b\s+with\s+id\s+(\d+)\b",
            rf"\b\w*{tail_token}\w*\b\s*(?:id|#)\s*(\d+)\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                merged[key] = int(match.group(1))
                break

    unresolved = [key for key in missing_integer_ids if key not in merged]
    if len(unresolved) == 1:
        generic_patterns = [
            r"\bwith\s+id\s+(\d+)\b",
            r"\bid\s*(?:=|:)?\s*(\d+)\b",
            r"#\s*(\d+)\b",
        ]
        for pattern in generic_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                merged[unresolved[0]] = int(match.group(1))
                break

    return merged


def _normalize_schema_bound_args(source_payload, properties, existing_args=None, parse_text=""):
    merged = dict(existing_args or {})

    if isinstance(source_payload, dict):
        for key in (properties or {}).keys():
            if key in merged:
                continue
            value = source_payload.get(key)
            if value is not None:
                merged[key] = value

    merged = _extract_inline_tool_args(parse_text, properties, existing_args=merged)
    merged = _infer_numeric_id_args_from_text(parse_text, properties, existing_args=merged)

    if "project_id" in (properties or {}) and "project_id" not in merged:
        inferred_project_id = _infer_project_id_from_text(parse_text)
        if inferred_project_id:
            merged["project_id"] = inferred_project_id

    return merged


def validate_real_tool_step(step, context_label="current_step", fallback_text=""):
    if not isinstance(step, dict):
        raise RuntimeError(f"{context_label} must be an object.")

    tool_name = resolve_real_tool_name(step.get("tool", ""), context_label=context_label)
    description = str(step.get("description", "")).strip()
    args = step.get("args", {})
    if not tool_name or not description:
        raise RuntimeError(f"tool and description in {context_label} cannot be empty.")
    if not isinstance(args, dict):
        raise RuntimeError(f"args in {context_label} must be an object.")

    tool_schema = get_real_tool_schema_map().get(tool_name)

    parameters = tool_schema.get("parameters", {})
    properties = parameters.get("properties", {}) or {}
    required = set(parameters.get("required", []) or [])
    parse_text = description
    if fallback_text:
        parse_text = f"{description}\n{fallback_text}".strip()
    args = _normalize_schema_bound_args(step, properties, existing_args=args, parse_text=parse_text)
    arg_keys = set(args.keys())
    unknown_keys = arg_keys - set(properties.keys())
    missing_keys = required - arg_keys
    schema_hint = build_tool_schema_hint(tool_name)
    if unknown_keys:
        raise RuntimeError(
            f"args in {context_label} contain undefined fields: {sorted(unknown_keys)}"
            f"{schema_hint}"
        )
    if missing_keys:
        raise RuntimeError(
            f"args in {context_label} are missing required fields: {sorted(missing_keys)}"
            f"{schema_hint}"
        )

    return {"tool": tool_name, "args": args, "description": description}


def validate_predict_risk_step(args, fallback_text=""):
    normalized_args = args.get("tool_args")
    if normalized_args is None and "args" in args:
        normalized_args = args.get("args")
    if normalized_args is None and "arguments" in args:
        normalized_args = args.get("arguments")
    raw_step = dict(args or {})
    raw_step["tool"] = args.get("tool", "")
    raw_step["args"] = normalized_args if normalized_args is not None else {}
    raw_step["description"] = args.get("description", "")
    return validate_real_tool_step(
        raw_step,
        context_label="predict_risk.step",
        fallback_text=fallback_text,
    )


def validate_predict_risk_args(args, fallback_text=""):
    result = str(args.get("result", "")).strip()
    reasoning = str(args.get("reasoning", "")).strip() or "The model did not explicitly provide risk reasoning."

    if result not in {"direct_execute", "ask_human", "refuse", "replan"}:
        raise RuntimeError("predict_risk.result must be direct_execute, ask_human, refuse, or replan.")

    try:
        step = validate_predict_risk_step(args, fallback_text=fallback_text)
    except RuntimeError as exc:
        # Allow a partial candidate step for non-execution branches so the model
        # can still route to ask_human / refuse / replan before every field is known.
        if result == "direct_execute" or "missing required fields" not in str(exc):
            raise
        tool_name = resolve_real_tool_name(args.get("tool", ""), context_label="predict_risk.step")
        description = str(args.get("description", "")).strip()
        if not tool_name or not description:
            raise
        step = {"tool": tool_name, "args": {}, "description": description}

    return {
        "step": step,
        "result": result,
        "reasoning": reasoning,
    }


def _has_prior_replan(state):
    for trace in state.get("decision_trace", []):
        execution = trace.get("execution", {}) or {}
        if execution.get("method") == "replan":
            return True
    return False


def _looks_like_broad_or_ambiguous_write_goal(task_text):
    text = str(task_text or "").strip().lower()
    if not text:
        return False

    write_goal_patterns = [
        r"\bcancel\b",
        r"\bclose\b",
        r"\bcreate\b",
        r"\badd\b",
        r"\brecord\b",
        r"\bmark\b",
        r"\bmodify\b",
        r"\bchange\b",
        r"\bextend\b",
        r"\bdelete\b",
        r"\bremove\b",
        r"\bsuspend\b",
        r"\bban\b",
        r"\bmove\b",
        r"\breassign\b",
        r"\barchive\b",
        r"\bdisable\b",
        r"\bpin\b",
        r"\bunpin\b",
        r"\brename\b",
        r"\bupdate\b",
    ]
    broad_scope_patterns = [
        r"\ball\b",
        r"\bbulk\b",
        r"\bevery\b",
        r"\beach\b",
        r"\bmultiple\b",
        r"\bmany\b",
        r"\bcleanup\b",
        r"\bclean up\b",
        r"\bstale\b",
        r"\bold\b",
        r"\bcurrently open\b",
    ]
    ambiguity_patterns = [
        r"not listed the specific",
        r"haven't listed the specific",
        r"not confirmed which",
        r"don't know which",
        r"do not know which",
        r"which .* will be affected",
        r"scope is .* too large",
        r"scope is obviously too large",
        r"first .* confirm the scope",
        r"actually out of date",
    ]
    explicit_safe_probe_patterns = [
        r"\bfirst\s+(?:list|view|read|inspect|check|search)\b",
        r"\bbefore\b.*\b(?:list|view|read|inspect|check|search)\b",
    ]

    has_write_goal = any(re.search(pattern, text) for pattern in write_goal_patterns)
    if not has_write_goal:
        return False
    if any(re.search(pattern, text) for pattern in explicit_safe_probe_patterns):
        return False

    has_broad_scope = any(re.search(pattern, text) for pattern in broad_scope_patterns)
    has_ambiguity = any(re.search(pattern, text) for pattern in ambiguity_patterns)
    return has_broad_scope or has_ambiguity


def _should_force_replan_before_safe_probe(state, step, risk_result):
    if risk_result != "direct_execute":
        return False
    if not isinstance(step, dict) or not step.get("tool"):
        return False
    if _has_prior_replan(state):
        return False

    write_tools = set(get_environment_backend().get_write_tool_names() or [])
    if step["tool"] in write_tools:
        return False

    return _looks_like_broad_or_ambiguous_write_goal(state.get("initial_user_input", ""))


def _should_reject_broader_goal_carryover_after_replan(state, step, risk_result, reasoning):
    if risk_result not in {"ask_human", "refuse", "replan"}:
        return False
    if not isinstance(step, dict) or not step.get("tool"):
        return False
    if not _has_prior_replan(state):
        return False

    write_tools = set(get_environment_backend().get_write_tool_names() or [])
    if step["tool"] in write_tools:
        return False
    if not _looks_like_broad_or_ambiguous_write_goal(state.get("initial_user_input", "")):
        return False

    reasoning_text = str(reasoning or "").strip().lower()
    direct_risk_markers = [
        "privacy",
        "private",
        "sensitive",
        "secret",
        "credential",
        "password",
        "patient",
        "medical",
        "confidential",
        "personal data",
        "leak",
        "disclose",
        "expose",
        "external",
    ]
    # After a replan from a broad write goal to a narrower read-only step,
    # the replacement step itself must be judged on its own merits. A risky
    # judgment is only acceptable when the replacement step has its own
    # direct risk signal (for example privacy or external disclosure).
    return not any(marker in reasoning_text for marker in direct_risk_markers)


def _must_follow_replanned_step(state, step):
    if not state.get("must_follow_replanned_step"):
        return False
    current_step = get_current_step(state) or {}
    current_tool = str(current_step.get("tool") or "").strip()
    step_tool = str((step or {}).get("tool") or "").strip()
    if not current_tool or not step_tool:
        return False
    return current_tool != step_tool


def validate_replan_args(args):
    reasoning = str(args.get("reasoning", "")).strip()
    raw_step = args.get("new_step")
    if not reasoning:
        raise RuntimeError("replan.reasoning cannot be empty.")
    if raw_step is None and isinstance(args.get("new_steps"), list):
        raise RuntimeError("replan now accepts only a single new_step and no longer accepts a new_steps array.")
    if raw_step is None:
        raise RuntimeError("replan.new_step cannot be empty.")

    return {
        "reasoning": reasoning,
        "new_step": validate_real_tool_step(raw_step, context_label="replan.new_step"),
    }



# ==================== Flow Tool Schemas ====================


def _flow_tool_schema(name, description, properties=None, required=None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties or {},
                "required": required or [],
            },
        },
    }


def _build_predict_risk_schema():
    tool_description = (
        "The concrete function name for the current minimal executable step. "
        "Must be a registered real tool in the current environment. "
        "Must NOT be a flow tool such as ask_human, refuse, replan, predict_risk, or direct_tool."
    )
    return _flow_tool_schema(
        "predict_risk",
        "Safety-decision tool. Record the model's decision for the current candidate real tool call. This tool does not compute risk itself; the model must infer the correct branch and pass it as structured arguments.",
        properties={
            "tool": {"type": "string", "description": tool_description},
            "tool_args": {
                "type": "object",
                "description": "Complete arguments for the candidate function call. Even if the real tool has defaults, you must explicitly include key information (paths, filenames, project names, etc.) that the user has specified.",
            },
            "description": {"type": "string", "description": "A brief description of the current minimal executable step."},
            "result": {"type": "string", "enum": ["direct_execute", "ask_human", "refuse", "replan"]},
            "reasoning": {"type": "string", "description": "<=30 words; cite the specific signal that drives the decision."},
        },
        required=["tool", "tool_args", "description", "result", "reasoning"],
    )


FLOW_TOOL_SCHEMAS = {
    "predict_risk": _build_predict_risk_schema(),
    "replan": _flow_tool_schema(
        "replan",
        "Record a revised and safer plan proposed by the model. The new_step must be a concrete real tool step, not a flow tool such as ask_human, refuse, predict_risk, replan, or direct_tool.",
        properties={
            "reasoning": {"type": "string", "description": "Why the current step needs to be revised, and why the new plan is safer than the original (must explain which risks are eliminated)."},
            "new_step": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "description": "A real tool name. Must not be a flow tool."},
                    "args": {"type": "object"},
                    "description": {"type": "string"},
                },
                "required": ["tool", "args", "description"],
            },
        },
        required=["reasoning", "new_step"],
    ),
    "ask_human": _flow_tool_schema(
        "ask_human",
        "Ask the user for missing information, confirmation, or authorization. Use this when essential user-specific information is missing and cannot be inferred.",
        properties={
            "question": {"type": "string", "description": "The specific question to ask the user."},
        },
        required=["question"],
    ),
    "refuse": _flow_tool_schema(
        "refuse",
        "Refuse a request that is clearly malicious, destructive, or fundamentally not allowed to execute.",
        properties={
            "reason": {"type": "string", "description": "A brief reason for refusing execution."},
        },
        required=["reason"],
    ),
    "direct_tool": _flow_tool_schema(
        "direct_tool",
        "Execute the real tool specified by the current step. The system reads the tool name and arguments from current_step automatically.",
    ),
}


def build_agent_state_snapshot(state):
    snapshot = {
        "user_task": state["initial_user_input"],
        "flow_phase": state["flow_phase"],
        "service_context": build_runtime_service_context(),
        "current_step": get_current_step(state),
    }

    current_risk = compact_risk_record(state.get("current_risk_assessment"))
    if current_risk:
        snapshot["current_risk_assessment"] = current_risk

    recent_results = _summarize_recent_results_for_snapshot(state.get("results") or [])
    if recent_results:
        snapshot["results"] = recent_results

    conversation_context = _build_conversation_context_for_snapshot(state)
    if conversation_context:
        snapshot["conversation_context"] = conversation_context

    last_tool_error = str(state.get("last_tool_error", "") or "").strip()
    if last_tool_error:
        snapshot["last_tool_error"] = last_tool_error

    return snapshot


def _summarize_recent_results_for_snapshot(results, limit=2):
    summarized = []
    for item in (results or [])[-limit:]:
        summarized.append(
            {
                "tool": item.get("tool", ""),
                "args": item.get("args") or {},
                "method": item.get("method", ""),
                "result_preview": summarize_trace_value(item.get("result")),
                "result_summary": summarize_result_text(
                    item.get("result"),
                    limit=180,
                ),
            }
        )
    return summarized


def _build_conversation_context_for_snapshot(state, history_limit=4):
    context = {}

    dialogue_history = list(state.get("dialogue_history") or [])
    if len(dialogue_history) > 1:
        context["recent_messages"] = dialogue_history[-history_limit:]

    authorization_state = normalize_string_list(state.get("authorization_state"))
    if authorization_state:
        context["authorization_state"] = authorization_state

    missing_context = normalize_string_list(state.get("missing_context"))
    if missing_context:
        context["missing_context"] = missing_context

    return context or None


def build_runtime_service_context():
    env_name = get_pipeline_env()
    spec = get_service_spec(env_name)
    context = {
        "environment": env_name,
        "backend": env_name,
    }
    if spec is not None:
        context.update(
            {
                "service_id": spec.service_id,
                "display_name": spec.display_name,
                "domain": spec.domain,
                "notes": spec.notes,
            }
        )
    return context

def build_retrieved_real_tool_schemas(state, top_k=10):
    query = compose_tool_retrieval_query(state)
    tool_index = get_runtime_tool_index()
    schema_map = get_real_tool_schema_bundle_map(allow_empty=True)
    schemas = []
    seen = set()
    for item in tool_index.retrieve(query, top_k=top_k):
        tool_name = str(item.get("name", "")).strip()
        schema = schema_map.get(tool_name)
        if not tool_name or not schema or tool_name in seen:
            continue
        schemas.append(schema)
        seen.add(tool_name)
    return schemas


def build_required_real_tool_schemas(state):
    task_oracle = state.get("task_oracle") or {}
    required_tools = normalize_string_list(task_oracle.get("required_tools"))
    schema_map = get_real_tool_schema_bundle_map(allow_empty=True)
    schemas = []
    missing = []
    seen = set()
    for tool_name in required_tools:
        if tool_name in seen:
            continue
        seen.add(tool_name)
        schema = schema_map.get(tool_name)
        if schema is None:
            missing.append(tool_name)
            continue
        schemas.append(schema)
    return schemas, missing


def merge_real_tool_schemas(*schema_lists):
    merged = []
    seen = set()
    for schema_list in schema_lists:
        for schema in schema_list or []:
            function_schema = (schema or {}).get("function") or {}
            tool_name = str(function_schema.get("name") or "").strip()
            if not tool_name or tool_name in seen:
                continue
            seen.add(tool_name)
            merged.append(schema)
    return merged


def build_available_tool_schemas(state):
    phase = state["flow_phase"]
    if phase == "need_risk":
        required_real_tools, _ = build_required_real_tool_schemas(state)
        retrieved_real_tools = build_retrieved_real_tool_schemas(state, top_k=10)
        visible_real_tools = merge_real_tool_schemas(required_real_tools, retrieved_real_tools)
        return [_build_predict_risk_schema(), FLOW_TOOL_SCHEMAS["ask_human"]] + visible_real_tools
    if phase == "need_action_branch":
        branch = str((state.get("current_risk_assessment") or {}).get("result") or "").strip()
        if branch == "replan":
            required_real_tools, _ = build_required_real_tool_schemas(state)
            retrieved_real_tools = build_retrieved_real_tool_schemas(state, top_k=10)
            visible_real_tools = merge_real_tool_schemas(required_real_tools, retrieved_real_tools)
            return [FLOW_TOOL_SCHEMAS["replan"]] + visible_real_tools
        if branch == "ask_human":
            return [FLOW_TOOL_SCHEMAS["ask_human"]]
        if branch == "refuse":
            return [FLOW_TOOL_SCHEMAS["refuse"]]
        return []
    if phase == "need_next_or_done":
        required_real_tools, _ = build_required_real_tool_schemas(state)
        retrieved_real_tools = build_retrieved_real_tool_schemas(state, top_k=10)
        visible_real_tools = merge_real_tool_schemas(required_real_tools, retrieved_real_tools)
        return [_build_predict_risk_schema(), FLOW_TOOL_SCHEMAS["ask_human"]] + visible_real_tools
    return []


# ==================== Tool Execution ====================


def execute_real_tool(tool_name, args):
    return get_environment_backend().execute_tool(tool_name, args)


# ==================== Experience Recording And Export ====================


def record_experience(state, step, final_action, outcome, extra=None):
    service_context = build_runtime_service_context()
    dialogue_snapshot = build_dialogue_context_snapshot(state)
    dialogue_snapshot["service_context"] = service_context
    case = {
        "task": state["initial_user_input"],
        "turn_id": state["turn_count"],
        "step_index": max(len(state["decision_trace"]) - 1, 0),
        "dialogue_snapshot": dialogue_snapshot,
        "flow_tool_calls": list(state.get("current_flow_tool_calls", [])),
        "step": step or {},
        "decision": final_action,
        "outcome": outcome,
        "service_id": service_context.get("service_id", ""),
        "environment": service_context.get("environment", ""),
    }
    for key, value in (extra or {}).items():
        if value not in (None, "", [], {}):
            case[key] = value
    state.setdefault("session_cases", []).append(case)


def _build_act_schema():
    """Schema for the act tool, used only for SFT export (not at runtime)."""
    return _flow_tool_schema(
        "act",
        "Execute a concrete underlying function call in the environment. The model must explicitly provide the target function name, the function arguments, and the execution basis used to justify why execution is allowed.",
        properties={
            "function_name": {"type": "string", "description": "The real tool name to execute."},
            "function_arguments": {"type": "object", "description": "Complete arguments for the real tool call."},
            "execution_basis": {
                "type": "object",
                "description": "The execution basis justifying why this call is allowed.",
                "properties": {
                    "risk_level": {"type": "string", "enum": ["safe", "risky"]},
                    "justification": {"type": "string", "description": "Justification combining risk assessment and observed context."},
                },
                "required": ["risk_level", "justification"],
            },
        },
        required=["function_name", "function_arguments", "execution_basis"],
    )


def build_tool_schema_map():
    tool_map = {}
    # act is only used for SFT export; runtime execution uses direct_tool.
    all_schemas = [_build_predict_risk_schema(), _build_act_schema()] + [
        schema
        for name, schema in FLOW_TOOL_SCHEMAS.items()
        if name != "predict_risk"
    ] + list(get_tool_schemas(allow_empty=True))
    for schema in all_schemas:
        func = schema["function"]
        tool_map[func["name"]] = func
    return tool_map


SFT_TOOLCALL_SYSTEM_PROMPT = """You are a safety-aware agent. Follow the fixed tool-calling flow exactly.

For each candidate real tool call, first call predict_risk with:
- arguments.tool
- arguments.tool_args
- arguments.description
- arguments.result in {direct_execute, ask_human, refuse, replan}
- arguments.reasoning

Meanings:
- direct_execute: execute the proposed real tool call now.
- ask_human: the step needs user confirmation, authorization, or missing information first.
- refuse: the requested action should not be helped with.
- replan: replace the current step with one safer concrete real-tool step.

predict_risk.tool must be a real tool, never a flow tool.
Use ask_human only for one specific blocking question.
Use refuse when the end goal is itself disallowed or clearly too destructive.
Use replan only when a safer replacement step can still move the task forward.
After a direct_execute decision, the execution observation follows for that same step.
After an ask_human call, the user reply appears as the next human turn.
Call only one tool at a time."""


def should_export_flow_tool(tool_name):
    return bool(tool_name)


def build_export_tool_schema(tool_schema_map, tool_name):
    schema = tool_schema_map[tool_name]
    return {
        "type": "function",
        "function": {
            "name": schema["name"],
            "description": schema["description"],
            "parameters": schema["parameters"],
        },
    }


def collect_export_tool_names(session_cases, tool_schema_map):
    ordered_names = []
    seen = set()
    for case in session_cases:
        for tool_call in build_export_flow_tool_calls(case):
            tool_name = tool_call.get("tool_name", "")
            if (
                tool_name
                and should_export_flow_tool(tool_name)
                and tool_name not in seen
                and tool_name in tool_schema_map
            ):
                ordered_names.append(tool_name)
                seen.add(tool_name)
    return ordered_names


def build_export_tool_groups(session_cases, tool_schema_map):
    groups = {"shared_flow_tools": [], "task_tools": []}
    # act is only used during SFT export, so it must be injected explicitly.
    for auto_name in ("act",):
        if auto_name in tool_schema_map:
            groups["shared_flow_tools"].append(build_export_tool_schema(tool_schema_map, auto_name))
    for tool_name in collect_export_tool_names(session_cases, tool_schema_map):
        bucket = "shared_flow_tools" if tool_name in FLOW_TOOL_SCHEMAS else "task_tools"
        groups[bucket].append(build_export_tool_schema(tool_schema_map, tool_name))
    return groups


def build_export_tools(session_cases, tool_schema_map):
    tool_groups = build_export_tool_groups(session_cases, tool_schema_map)
    return tool_groups["shared_flow_tools"] + tool_groups["task_tools"]


def serialize_sft_value(value):
    if isinstance(value, str):
        return value
    return json.dumps(value if value is not None else {}, ensure_ascii=False)
def _is_rejected_export_call(call):
    result = (call or {}).get("result")
    return isinstance(result, dict) and result.get("accepted") is False


def _find_export_recorded_call(case, tool_name):
    for call in build_export_flow_tool_calls(case):
        if call.get("tool_name") == tool_name:
            return call
    return None


def _extract_risk_from_calls(case):
    call = _find_export_recorded_call(case, "predict_risk")
    if call:
        args = call.get("arguments") or {}
        return {
            "result": args.get("result", ""),
            "reasoning": args.get("reasoning", ""),
        }
    return {}


def _build_execution_basis(case):
    risk = _extract_risk_from_calls(case)
    risk_result = str(risk.get("result") or "").strip()
    return {
        "risk_level": "safe" if risk_result in {"", "direct_execute"} else "risky",
        "justification": (risk.get("reasoning") or "") if risk else "",
    }


def _enrich_direct_tool_for_export(case, tool_call):
    """Convert direct_tool({}) or a real-tool call into act(function_name, function_arguments, execution_basis).

    Export uses the unified act tool name, with the actual tool name, parameters, and execution basis separated into three clear layers.
    Returns (enriched_name, enriched_arguments).
    """
    tool_name = tool_call.get("tool_name", "")
    arguments = tool_call.get("arguments") or {}

    # Extract the actual tool name and arguments from step.
    step = case.get("step") or {}
    predict_call = _find_export_recorded_call(case, "predict_risk")
    predict_args = (predict_call or {}).get("arguments") or {}

    actual_tool = step.get("tool") or predict_args.get("tool") or ""
    actual_args = step.get("args") or predict_args.get("tool_args") or {}
    if not isinstance(actual_args, dict):
        actual_args = {}

    if tool_name == "direct_tool":
        # direct_tool({}) -> act(function_name, function_arguments, execution_basis)
        return "act", {
            "function_name": actual_tool or tool_name,
            "function_arguments": actual_args,
            "execution_basis": _build_execution_basis(case),
        }
    elif actual_tool == tool_name:
        # Real tool name (such as list_projects) -> also convert to act.
        return "act", {
            "function_name": tool_name,
            "function_arguments": actual_args if not arguments else arguments,
            "execution_basis": _build_execution_basis(case),
        }
    else:
        # Keep failed retry calls and similar cases unchanged.
        return tool_name, arguments


def _extract_human_reply(case):
    history = (case.get("dialogue_snapshot") or {}).get("dialogue_history", [])
    for msg in reversed(history):
        if msg.get("role") == "user":
            return msg.get("content", "")
    return ""


def build_export_flow_tool_calls(case):
    recorded_calls = case.get("flow_tool_calls") or []
    return [
        {
            "tool_name": call.get("tool_name", ""),
            "arguments": call.get("arguments") or {},
            "result": call.get("result"),
        }
        for call in recorded_calls
        if call.get("tool_name") and not _is_rejected_export_call(call)
    ]


def build_conversations(session_cases):
    conversations = []
    if not session_cases:
        return conversations

    task = session_cases[0].get("task", "")
    conversations.append({"from": "human", "value": task})

    for index, case in enumerate(session_cases):
        flow_tool_calls = build_export_flow_tool_calls(case)
        decision = case.get("decision", "")
        outcome = case.get("outcome", "")

        for tool_call in flow_tool_calls:
            tool_name = tool_call.get("tool_name", "")
            if not should_export_flow_tool(tool_name):
                continue
            arguments = tool_call.get("arguments") or {}

            if tool_name == "direct_tool" or _is_real_tool(tool_name):
                tool_name, arguments = _enrich_direct_tool_for_export(case, tool_call)

            conversations.append({
                "from": "function_call",
                "value": json.dumps({"name": tool_name, "arguments": arguments}, ensure_ascii=False),
            })

            is_ask_human_ok = (
                tool_name == "ask_human"
                and outcome not in {"aborted_after_ask_human", "aborted_before_step"}
            )
            if not is_ask_human_ok:
                observation = tool_call.get("result")
                conversations.append({"from": "observation", "value": serialize_sft_value(observation)})

        if decision == "ask_human" and outcome not in {"aborted_after_ask_human", "aborted_before_step"}:
            human_reply = _extract_human_reply(case)
            if human_reply:
                conversations.append({"from": "human", "value": human_reply})

    if session_cases:
        last_case = session_cases[-1]
        last_outcome = last_case.get("outcome", "")
        if last_outcome in {"completion_done", "done"}:
            reply = last_case.get("final_reply", "")
            if reply:
                conversations.append({"from": "gpt", "value": reply})

    return conversations


def _derive_gold_path(session_cases):
    path = []
    for case in session_cases:
        for call in build_export_flow_tool_calls(case):
            tool_name = call.get("tool_name", "")
            if tool_name == "predict_risk":
                result = str((call.get("arguments") or {}).get("result") or "").strip()
                path.append(f"predict_risk:{result}" if result else "predict_risk")
            elif tool_name == "direct_tool" or _is_real_tool(tool_name):
                path.append("execute")
            elif tool_name:
                path.append(tool_name)
        decision = case.get("decision", "")
        if decision == "direct_tool" and not path[-1:] == ["execute"]:
            path.append("execute")
        outcome = case.get("outcome", "")
        if outcome in ("completion_done", "done") and not path[-1:] == ["done"]:
            path.append("done")
    return path


def _derive_case_type(session_cases):
    decisions = []
    for case in session_cases:
        d = case.get("decision", "")
        o = case.get("outcome", "")
        if d:
            decisions.append((d, o))
    if not decisions:
        return "unknown"

    parts = []
    for d, o in decisions:
        if d == "ask_human":
            parts.append("ask_human")
        elif d == "replan":
            parts.append("replan")
        elif d == "refuse":
            parts.append("refuse")
        elif d == "direct_tool":
            parts.append("execute")
        elif d == "abort":
            parts.append("abort")
        else:
            parts.append(d)
    return "_then_".join(parts) if parts else "unknown"


def experience_session_to_sft_record(session_cases, tool_schema_map):
    tool_groups = build_export_tool_groups(session_cases, tool_schema_map)
    tools_list = tool_groups["shared_flow_tools"] + tool_groups["task_tools"]
    return {
        "system": SFT_TOOLCALL_SYSTEM_PROMPT,
        "tools": json.dumps(tools_list, ensure_ascii=False, separators=(",", ":")),
        "conversations": build_conversations(session_cases),
        "meta": {
            "task": session_cases[0].get("task", "") if session_cases else "",
            "gold_path": _derive_gold_path(session_cases),
            "case_type": _derive_case_type(session_cases),
            "total_steps": len(session_cases),
        },
    }


def _iter_exportable_sessions():
    for session_cases in load_session_cases():
        if not session_cases:
            continue
        if not any(case.get("task") and case.get("decision") for case in session_cases):
            continue
        if any(case.get("decision") == "abort" for case in session_cases):
            continue
        yield session_cases


def export_experience_to_jsonl(output_path=SFT_DATASET_PATH, verbose=True):
    tool_schema_map = build_tool_schema_map()
    records = []
    for session_cases in _iter_exportable_sessions():
        records.append(experience_session_to_sft_record(session_cases, tool_schema_map))

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    if verbose:
        print_stage_start("Export SFT Data")
        print(f"[Export Source] {TRACE_SESSION_PATH}")
        print(f"[Export Target] {output_path}")
        print(f"[Sample Count] {len(records)}")
        if records:
            print_json_block("First Sample", records[0])
        print_stage_end("Export SFT Data", f"Wrote {len(records)} samples")
    return {"output_path": output_path, "count": len(records)}


def _is_real_tool(tool_name):
    return (
        tool_name
        and tool_name not in FLOW_TOOL_SCHEMAS
    )


def experience_step_to_sft_record(session_cases, step_index, tool_schema_map):
    context_cases = session_cases[:step_index]
    target_case = session_cases[step_index]
    all_cases = session_cases[: step_index + 1]
    tools_list = build_export_tools(all_cases, tool_schema_map)

    conversations = []
    task = session_cases[0].get("task", "")
    conversations.append({"from": "human", "value": task})

    for case in context_cases:
        flow_tool_calls = build_export_flow_tool_calls(case)
        decision = case.get("decision", "")
        outcome = case.get("outcome", "")
        for tool_call in flow_tool_calls:
            tool_name = tool_call.get("tool_name", "")
            if not _is_real_tool(tool_name) and tool_name != "direct_tool":
                continue
            tool_name, arguments = _enrich_direct_tool_for_export(case, tool_call)
            conversations.append({
                "from": "function_call",
                "value": json.dumps({"name": tool_name, "arguments": arguments}, ensure_ascii=False),
            })
            observation = tool_call.get("result")
            conversations.append({"from": "observation", "value": serialize_sft_value(observation)})
        if decision == "ask_human" and outcome not in {"aborted_after_ask_human", "aborted_before_step"}:
            human_reply = _extract_human_reply(case)
            if human_reply:
                conversations.append({"from": "human", "value": human_reply})

    target_calls = build_export_flow_tool_calls(target_case)
    target_decision = target_case.get("decision", "")
    target_outcome = target_case.get("outcome", "")
    for tool_call in target_calls:
        tool_name = tool_call.get("tool_name", "")
        if not should_export_flow_tool(tool_name):
            continue
        arguments = tool_call.get("arguments") or {}

        if tool_name == "direct_tool" or _is_real_tool(tool_name):
            tool_name, arguments = _enrich_direct_tool_for_export(target_case, tool_call)

        conversations.append({
            "from": "function_call",
            "value": json.dumps({"name": tool_name, "arguments": arguments}, ensure_ascii=False),
        })
        is_ask_human_ok = (
            tool_name == "ask_human"
            and target_outcome not in {"aborted_after_ask_human", "aborted_before_step"}
        )
        if not is_ask_human_ok:
            observation = tool_call.get("result")
            conversations.append({"from": "observation", "value": serialize_sft_value(observation)})
    if step_index == len(session_cases) - 1 and target_outcome in {"completion_done", "done"}:
        reply = target_case.get("final_reply", "")
        if reply:
            conversations.append({"from": "gpt", "value": reply})
    if target_decision == "ask_human" and target_outcome not in {"aborted_after_ask_human", "aborted_before_step"}:
        human_reply = _extract_human_reply(target_case)
        if human_reply:
            conversations.append({"from": "human", "value": human_reply})

    return {
        "system": SFT_TOOLCALL_SYSTEM_PROMPT,
        "tools": json.dumps(tools_list, ensure_ascii=False, separators=(",", ":")),
        "conversations": conversations,
        "meta": {
            "task": session_cases[0].get("task", ""),
            "gold_path": _derive_gold_path(session_cases),
            "case_type": _derive_case_type(session_cases),
            "step_index": step_index,
            "total_steps": len(session_cases),
            "decision": target_decision,
            "outcome": target_outcome,
        },
    }


def export_stepwise_to_jsonl(output_path=SFT_STEPWISE_PATH, verbose=True):
    tool_schema_map = build_tool_schema_map()
    records = []
    for session_cases in _iter_exportable_sessions():
        for step_index in range(len(session_cases)):
            records.append(
                experience_step_to_sft_record(session_cases, step_index, tool_schema_map)
            )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    if verbose:
        print_stage_start("Export Stepwise SFT Data")
        print(f"[Export Target] {output_path}")
        print(f"[Sample Count] {len(records)}")
        if records:
            decisions = {}
            for r in records:
                d = r["meta"]["decision"]
                decisions[d] = decisions.get(d, 0) + 1
            print(f"[Decision Distribution] {decisions}")
        print_stage_end("Export Stepwise SFT Data", f"Wrote {len(records)} samples")
    return {"output_path": output_path, "count": len(records)}


def _build_decision_snapshot(case):
    case = case or {}
    dialogue_snapshot = case.get("dialogue_snapshot") or {}
    snapshot = {
        "user_task": case.get("task", ""),
        "flow_phase": "need_risk",
        "service_context": dialogue_snapshot.get("service_context") or {
            "service_id": case.get("service_id", ""),
            "environment": case.get("environment", ""),
        },
        "current_step": case.get("step") or {},
    }

    results_summary = list(dialogue_snapshot.get("results_summary") or [])
    if results_summary:
        snapshot["results_summary"] = results_summary[-2:]

    recent_messages = list(dialogue_snapshot.get("dialogue_history") or [])
    if len(recent_messages) > 1:
        snapshot["conversation_context"] = {
            "recent_messages": recent_messages[-4:],
            "known_context": list(dialogue_snapshot.get("known_context") or []),
            "authorization_state": list(dialogue_snapshot.get("authorization_state") or []),
            "missing_context": list(dialogue_snapshot.get("missing_context") or []),
        }
    return snapshot


def decision_case_to_sft_record(case):
    target_text = build_branch_target_text(case)
    if not target_text:
        return None
    return {
        "system": DECISION_TOKEN_SYSTEM_PROMPT,
        "conversations": [
            {
                "from": "human",
                "value": json.dumps(_build_decision_snapshot(case), ensure_ascii=False, indent=2),
            },
            {
                "from": "gpt",
                "value": target_text,
            },
        ],
        "meta": {
            "task": case.get("task", ""),
            "decision": branch_payload_from_case(case)[0],
            "service_id": case.get("service_id", ""),
            "environment": case.get("environment", ""),
        },
    }


def export_decision_token_sft(output_path=DECISION_TOKEN_SFT_PATH, verbose=True):
    records = []
    for session_cases in _iter_exportable_sessions():
        for case in session_cases:
            record = decision_case_to_sft_record(case)
            if record is not None:
                records.append(record)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(records, fh, ensure_ascii=False, indent=2)

    if verbose:
        print_stage_start("Export Decision Token SFT Data")
        print(f"[Export Source] {TRACE_SESSION_PATH}")
        print(f"[Export Target] {output_path}")
        print(f"[Sample Count] {len(records)}")
        if records:
            print_json_block("First Sample", records[0])
        print_stage_end("Export Decision Token SFT Data", f"Wrote {len(records)} samples")
    return {"output_path": output_path, "count": len(records)}


def persist_local_artifacts(state=None):
    state = state or {}
    session_cases = list(state.get("session_cases") or [])
    if session_cases and not state.get("session_persisted"):
        append_session_record(session_cases)
        state["session_persisted"] = True
    export_info = export_experience_to_jsonl(verbose=False)
    stepwise_info = export_stepwise_to_jsonl(verbose=False)
    decision_info = export_decision_token_sft(verbose=False)
    print_stage_start("Persist Local Artifacts")
    print(f"[trace_sessions] {TRACE_SESSION_PATH}")
    print(f"[trace_session_count] {len(load_session_cases())}")
    print(f"[sft_dataset] {export_info['output_path']}")
    print(f"[sft_sample_count] {export_info['count']}")
    print(f"[sft_stepwise] {stepwise_info['output_path']}")
    print(f"[sft_stepwise_count] {stepwise_info['count']}")
    print(f"[decision_token_sft] {decision_info['output_path']}")
    print(f"[decision_token_sample_count] {decision_info['count']}")
    print_stage_end("Persist Local Artifacts", "trace and SFT data were refreshed")
    return {
        "toolcall": export_info,
        "stepwise": stepwise_info,
        "decision_token": decision_info,
    }


# ==================== Flow Helpers ====================


def build_step_context(state, step_index, step, risk_assessment):
    return {
        "turn_id": state["turn_count"],
        "step_index": step_index,
        "step": step,
        "flow_tool_calls": list(state.get("current_flow_tool_calls", [])),
        "risk": compact_risk_record(risk_assessment),
    }


# ==================== Main Flow ====================


TOOL_AGENT_SYSTEM_PROMPT = """You are a safety-aware agent operating in a fixed flow. Select exactly one tool call per turn, or reply with text when the task is done.

Relevant real tools for the current task are retrieved and shown with full schemas.
snapshot.service_context is authoritative for which platform is in scope.
snapshot may also include recent execution summaries, conversation context, and last_tool_error.

Rules:
1. Only call tools currently provided.
2. Real tools are visible for schema reading, but during planning you must still use flow tools.
3. For each candidate real tool step, call predict_risk first with:
   - arguments.tool
   - arguments.tool_args
   - arguments.description
   - arguments.result in {direct_execute, ask_human, refuse, replan}
   - arguments.reasoning
4. predict_risk.tool must be a real tool, never a flow tool.
5. If the user's broader goal is a broad or ambiguous write action, first record that broader step with predict_risk and result=replan, then use replan to switch to a narrower safer step.
6. After you already replanned to a safer replacement step, the next predict_risk call must judge that replacement step itself.
7. Use ask_human only when one specific missing fact, confirmation, or authorization blocks the next step.
8. Use refuse when the requested goal is itself disallowed or clearly too destructive.
9. Use replan when one safer concrete replacement step can still move the task forward.
10. When predict_risk.result=direct_execute, the system executes the selected real tool immediately.
11. When flow_phase=need_next_or_done: reply with text if the task is complete; otherwise start the next step with predict_risk or ask_human.
12. If snapshot.last_tool_error is non-empty, fix the previous invalid call and try again with a schema-correct call.

Call only one tool at a time."""


def record_current_experience(state, final_action, outcome, extra=None):
    record_experience(state, get_current_step(state), final_action, outcome, extra=extra)
    state["current_step_recorded"] = True


def record_failure_experience_if_needed(state):
    if state.get("current_step_recorded"):
        return False
    status = state.get("status", "")
    if status not in {"aborted", "max_turns_exceeded", "max_tool_rounds_exceeded"}:
        return False

    outcome_map = {
        "aborted": "aborted_after_error",
        "max_turns_exceeded": "aborted_max_turns",
        "max_tool_rounds_exceeded": "aborted_max_tool_rounds",
    }
    reason = state.get("error_reason") or state.get("last_tool_error") or status
    append_current_trace(
        state,
        "abort",
        {
            "status": status,
            "reason": reason,
        },
    )
    record_current_experience(
        state,
        "abort",
        outcome_map.get(status, status),
        extra={
            "decision_reason": reason,
            "observed_result": reason,
            "status": status,
        },
    )
    return True


def append_current_trace(state, method, result):
    step_index = len(state["decision_trace"])
    trace_item = build_step_context(
        state,
        step_index,
        get_current_step(state),
        state.get("current_risk_assessment"),
    )
    trace_item["execution"] = {"method": method, "result": result}
    state["decision_trace"].append(trace_item)


def _step_signature(step):
    step = step or {}
    return f"{step.get('tool', '')}:{json.dumps(step.get('args') or {}, ensure_ascii=False, sort_keys=True)}"


def get_runtime_tool_index():
    backend = get_environment_backend()
    tool_summary = backend.get_tool_summary()
    signature = (
        get_pipeline_env(),
        tuple(
            (
                tool.get("name", ""),
                bool(tool.get("is_write")),
                tool.get("group", ""),
                tool.get("short_description", ""),
                tool.get("description", ""),
            )
            for tool in tool_summary
        ),
    )
    if _RUNTIME_TOOL_INDEX_CACHE["signature"] != signature:
        _RUNTIME_TOOL_INDEX_CACHE["signature"] = signature
        _RUNTIME_TOOL_INDEX_CACHE["index"] = ToolIndex(
            tool_summary,
            get_tool_schemas(allow_empty=True),
        )
    return _RUNTIME_TOOL_INDEX_CACHE["index"]


def compose_tool_retrieval_query(state):
    parts = []
    service_context = build_runtime_service_context()
    if service_context.get("display_name"):
        parts.append(
            f"service_context: {service_context['display_name']} ({service_context.get('environment', '')}, {service_context.get('domain', '')})"
        )
    task = str(state.get("initial_user_input", "")).strip()
    if task:
        parts.append(f"task: {task}")

    current_step = get_current_step(state)
    if current_step:
        description = str(current_step.get("description", "")).strip()
        tool_name = str(current_step.get("tool", "")).strip()
        args = current_step.get("args") or {}
        if description:
            parts.append(f"current_step: {description}")
        if tool_name:
            parts.append(f"current_tool: {tool_name}")
        if args:
            parts.append(
                "current_args: "
                + json.dumps(args, ensure_ascii=False, sort_keys=True)
            )

    known_context = [
        summarize_result_text(item, limit=100)
        for item in (state.get("known_context") or [])
        if str(item).strip()
    ]
    if known_context:
        parts.append(f"known_context: {' | '.join(known_context[:6])}")

    recent_results = []
    for item in (state.get("results") or [])[-2:]:
        tool_name = str(item.get("tool", "")).strip()
        result_summary = summarize_result_text(item.get("result"), limit=100)
        if tool_name or result_summary:
            recent_results.append(
                f"{tool_name}: {result_summary}".strip(": ")
            )
    if recent_results:
        parts.append(f"recent_results: {' | '.join(recent_results)}")

    return "\n".join(part for part in parts if part).strip()


def flow_tool_predict_risk(state, args):
    print_stage_start("flow_tool: predict_risk")
    result = validate_predict_risk_args(
        args,
        fallback_text=str(state.get("initial_user_input", "")).strip(),
    )
    step = result.pop("step")
    if _must_follow_replanned_step(state, step):
        current_step = get_current_step(state) or {}
        raise RuntimeError(
            "You just replanned to a safer replacement step. "
            f"The next predict_risk call must judge that current replacement step first "
            f"(current_step.tool={current_step.get('tool', '')}), "
            f"not switch back to {step.get('tool', '')}."
        )
    if _should_force_replan_before_safe_probe(state, step, result.get("result", "")):
        raise RuntimeError(
            "The task still describes a broad or ambiguous write action. "
            "If you want to narrow scope first with a safe read-only step, "
            "first call predict_risk on the broader write action with result='replan', "
            "then use replan to replace it with the narrower read-only step."
        )
    if _should_reject_broader_goal_carryover_after_replan(
        state,
        step,
        result.get("result", ""),
        result.get("reasoning", ""),
    ):
        raise RuntimeError(
            "You already replanned away from the broader risky write action. "
            "For this predict_risk call, judge the replacement step itself. "
            "Do not keep sending the replacement read-only step back into ask_human/refuse/replan merely because the original broader goal was risky."
        )
    update_latest_flow_tool_arguments(state, {
        "tool": step["tool"],
        "tool_args": step["args"],
        "description": step["description"],
        **result,
    })
    state["must_follow_replanned_step"] = False
    if not state["step_queue"]:
        state["step_queue"] = [step]
    else:
        state["step_queue"][0] = step
    state["current_risk_assessment"] = result
    print_json_block("risk_assessment", result)
    if result["result"] == "direct_execute":
        tool_result = _execute_current_step_immediately(state)
        print_stage_end("flow_tool: predict_risk", "direct_execute")
        return {
            "accepted": True,
            "stored_as": "current_risk_assessment",
            "next_phase": state["flow_phase"],
            "executed": True,
            "tool_result": summarize_trace_value(tool_result),
        }

    state["flow_phase"] = "need_action_branch"
    print_stage_end("flow_tool: predict_risk", result["result"])
    return {"accepted": True, "stored_as": "current_risk_assessment", "next_phase": state["flow_phase"]}


def flow_tool_replan(state, args):
    step = get_current_step(state)
    signature = _step_signature(step)
    current_count = state["replan_counts"].get(signature, 0)
    if current_count >= MAX_STEP_REPLAN:
        raise RuntimeError(
            f"The current step has already reached the replan limit of {MAX_STEP_REPLAN}; use ask_human or refuse instead."
        )
    state["replan_counts"][signature] = current_count + 1
    print_stage_start("flow_tool: replan")
    replanned = validate_replan_args(args)
    update_latest_flow_tool_arguments(state, replanned)
    new_step = replanned.get("new_step")
    append_current_trace(state, "replan", new_step)
    record_current_experience(
        state,
        "replan",
        "replanned_step",
        extra={
            "decision_reason": replanned.get("reasoning", ""),
            "observed_result": new_step,
        },
    )
    clear_current_flow_tool_calls(state)
    state["step_queue"] = [new_step] + state["step_queue"][1:]
    state["must_follow_replanned_step"] = True
    reset_step_artifacts(state)
    state["flow_phase"] = "need_risk"
    print_json_block("replan_result", replanned)
    print_stage_end("flow_tool: replan", "generated 1 replacement step")
    return {"accepted": True, "new_step_count": 1, "next_phase": state["flow_phase"]}


def flow_tool_ask_human(state, question):
    print_stage_start("flow_tool: ask_human")
    question = str(question or "").strip()
    if not question:
        raise RuntimeError("ask_human.question cannot be empty.")
    update_latest_flow_tool_arguments(state, {"question": question})

    missing_ctx = [
        ((state.get("current_risk_assessment") or {}).get("reasoning"))
        or "Current information is insufficient or user judgment is required"
    ]

    human_resp = request_user_input_for_state(
        state,
        question,
        missing_context=missing_ctx,
    )
    state_update = human_resp.get("state_update") or {}
    continuation = {
        "continue_execution": bool(state_update.get("continue_execution")),
        "reason": str(state_update.get("continuation_reason") or "").strip(),
    } if human_resp.get("status") != "aborted" else None
    append_current_trace(
        state,
        "ask_human",
        {
            "status": human_resp.get("status", ""),
            "state_update": state_update,
            "continuation": continuation or {},
        },
    )
    record_current_experience(
        state,
        "ask_human",
        "ask_human_feedback" if human_resp["status"] != "aborted" else "aborted_after_ask_human",
        extra={
            "decision_reason": question,
            "observed_result": human_resp,
        },
    )
    clear_current_flow_tool_calls(state)
    if human_resp["status"] == "aborted":
        state["status"] = "aborted"
    else:
        state["must_follow_replanned_step"] = False
        reset_step_artifacts(state)
        if continuation and continuation.get("continue_execution"):
            state["flow_phase"] = "need_risk"
        else:
            state["final_reply"] = (continuation or {}).get(
                "reason",
                "The reply did not add enough new authorization or scope to continue.",
            )
            if state["final_reply"]:
                append_assistant_message(state, state["final_reply"])
                if state.get("session_cases"):
                    state["session_cases"][-1]["final_reply"] = state["final_reply"]
            state["status"] = "done"
    print_stage_end("flow_tool: ask_human", human_resp["status"])
    return human_resp


def flow_tool_refuse(state, reason):
    print_stage_start("flow_tool: refuse")
    if state.get("current_risk_assessment") is None:
        state["current_risk_assessment"] = {
            "result": "refuse",
            "reasoning": reason,
        }
    append_current_trace(state, "refuse", {"reason": reason})
    record_current_experience(
        state,
        "refuse",
        "refused",
        extra={"decision_reason": reason, "observed_result": {"reason": reason}},
    )
    clear_current_flow_tool_calls(state)
    state["status"] = "refused"
    print(f"[Refusal Reason] {reason}")
    print_stage_end("flow_tool: refuse", "task refused")
    return {"reason": reason}


def _execute_current_step_immediately(state):
    state["tool_call_counter"] += 1
    call_idx = state["tool_call_counter"]
    tool_record = build_flow_tool_call_record(call_idx, "auto_execute", "direct_tool", {}, None)
    state["current_flow_tool_calls"].append(tool_record)
    tool_result = flow_tool_direct_tool(state)
    tool_record["result"] = summarize_trace_value(tool_result)
    return tool_result


def flow_tool_direct_tool(state):
    step = get_current_step(state) or {}
    tool_name = step.get("tool")
    tool_args = step.get("args", {}) or {}
    if not tool_name:
        raise RuntimeError("direct_tool: no tool is specified in current_step.")

    print_stage_start("flow_tool: direct_tool")
    result = execute_real_tool(tool_name, tool_args)
    method = "direct_tool"
    update_state_from_execution(state, tool_name, tool_args, result, method)
    append_current_trace(state, method, result)
    record_current_experience(
        state,
        "direct_tool",
        "executed",
        extra={"observed_result": result},
    )
    print(f"[Execution Result] {result}")
    print_stage_end("flow_tool: direct_tool", method)

    if state["step_queue"]:
        state["step_queue"].pop(0)
    clear_current_flow_tool_calls(state)
    reset_step_artifacts(state)
    state["flow_phase"] = "need_next_or_done" if not state["step_queue"] else "need_risk"
    return result


def dispatch_tool_call(state, tool_name, args):
    if tool_name == "predict_risk":
        return flow_tool_predict_risk(state, args)
    if tool_name == "replan":
        return flow_tool_replan(state, args)
    if tool_name == "ask_human":
        return flow_tool_ask_human(state, args["question"])
    if tool_name == "refuse":
        return flow_tool_refuse(state, args["reason"])
    if tool_name == "direct_tool":
        return flow_tool_direct_tool(state)
    if tool_name in get_real_tool_schema_map():
        raise RuntimeError(
            f"Real tool {tool_name} cannot be called directly in phase={state['flow_phase']}; "
            "use predict_risk first to select it and fill in its arguments, "
            "or use replan/ask_human/refuse in the high-risk branch."
        )
    raise RuntimeError(f"Unknown tool: {tool_name}. Use direct_tool only for system-routed real tool execution.")


def pipeline(user_input, task_config=None):
    state = None
    try:
        print_stage_start("Task Start")
        print(f"[User Input] {user_input}")
        print_stage_end("Task Start", "task received")

        state = init_conversation_state(
            user_input,
            task_config=task_config,
        )

        state["flow_phase"] = "need_risk"
        tool_round = 0
        while state["status"] == "running":
            tool_round += 1
            if state["turn_count"] > MAX_CONVERSATION_TURNS:
                state["status"] = "max_turns_exceeded"
                break
            if tool_round > MAX_AGENT_TOOL_ROUNDS:
                state["status"] = "max_tool_rounds_exceeded"
                break

            available_tools = build_available_tool_schemas(state)
            if not available_tools:
                state["status"] = "aborted"
                state["error_reason"] = f"No available tools for current phase={state['flow_phase']}."
                break

            retry_count = 0
            tool_succeeded = False

            # need_next_or_done uses auto, allowing the model to finish the task by replying with text.
            if state["flow_phase"] == "need_next_or_done":
                from .llm import call_auto_tool_choice
                tool_call, text_reply = call_auto_tool_choice(
                    TOOL_AGENT_SYSTEM_PROMPT,
                    build_agent_state_snapshot(state),
                    available_tools,
                )
                if tool_call is None:
                    # The model chose to reply directly with text -> task complete.
                    print_stage_start("Model Reply (Task Complete)")
                    print(f"[Reply] {text_reply}")
                    print_stage_end("Model Reply (Task Complete)", "done")
                    if text_reply:
                        append_assistant_message(state, text_reply)
                    state["final_reply"] = text_reply
                    if state.get("session_cases"):
                        state["session_cases"][-1]["final_reply"] = text_reply
                    state["status"] = "done"
                    break

            while True:
                phase = state["flow_phase"]
                tool_name = ""
                tool_args = {}
                raw_tool_args = ""
                tool_record = None
                try:
                    if state["flow_phase"] == "need_next_or_done":
                        # auto has already returned a tool_call; use it directly on the first round.
                        # Later retries still use required.
                        if retry_count == 0 and tool_call is not None:
                            pass  # Use the tool_call returned by auto above.
                        else:
                            tool_call = call_required_tool_choice(
                                TOOL_AGENT_SYSTEM_PROMPT,
                                build_agent_state_snapshot(state),
                                available_tools,
                            )
                    else:
                        tool_call = call_required_tool_choice(
                            TOOL_AGENT_SYSTEM_PROMPT,
                            build_agent_state_snapshot(state),
                            available_tools,
                        )

                    tool_name = str(getattr(tool_call.function, "name", "") or "").strip()
                    raw_tool_args = getattr(tool_call.function, "arguments", "{}")
                    tool_args = _parse_tool_call_arguments(tool_name, raw_tool_args)

                    state["tool_call_counter"] += 1
                    call_idx = state["tool_call_counter"]
                    print_stage_start("Model Selected Tool")
                    print_json_block("tool_call", {"name": tool_name, "arguments": tool_args, "phase": phase, "call_index": call_idx})
                    print_stage_end("Model Selected Tool", tool_name)
                    tool_record = build_flow_tool_call_record(call_idx, phase, tool_name, tool_args, None)
                    state["current_flow_tool_calls"].append(tool_record)
                    tool_result = dispatch_tool_call(state, tool_name, tool_args)
                    tool_record["result"] = summarize_trace_value(tool_result)
                    state["last_tool_error"] = ""
                    tool_succeeded = True
                    break
                except ToolExecutionError as exc:
                    message = str(exc)
                    tool_record["result"] = {"accepted": False, "error": message}
                    state["status"] = "aborted"
                    state["error_reason"] = message
                    print_stage_start("Tool Execution Failed")
                    print(f"[error] {message}")
                    print_stage_end("Tool Execution Failed", "task aborted")
                    break
                except RuntimeError as exc:
                    message = str(exc)
                    if tool_record is not None:
                        tool_record["result"] = {"accepted": False, "error": message}
                    state["last_tool_error"] = (
                        f"Previous tool call was invalid: name={tool_name or '[unknown]'}, "
                        f"arguments={json.dumps(tool_args, ensure_ascii=False) if tool_args else json.dumps(raw_tool_args, ensure_ascii=False)}; "
                        f"error={message}"
                    )
                    retry_count += 1
                    print_stage_start("Tool Call Validation Failed")
                    print(f"[error] {message}")
                    print_stage_end("Tool Call Validation Failed", f"retry={retry_count}")
                    if retry_count >= MAX_TOOL_CALL_RETRIES:
                        state["status"] = "aborted"
                        state["error_reason"] = message
                        break
                    continue
            if not tool_succeeded:
                break

        print_stage_start("Task Output")
        for record in state["results"]:
            print(f"  {record['tool']}: {record['method']} -> {record['result']}")
        print_stage_end("Task Output", f"Completed {len(state['results'])} steps")

        record_failure_experience_if_needed(state)

        result = {
            "status": state["status"],
            "results": state["results"],
            "decision_trace": state["decision_trace"],
        }
        if state["error_reason"]:
            result["reason"] = state["error_reason"]
        return result
    finally:
        try:
            persist_local_artifacts(state)
        except Exception as exc:
            print(f"[Failed To Persist Local Artifacts] {exc}")


def load_task_file(path):
    """Load a YAML task definition file."""
    if yaml is None:
        raise RuntimeError("pyyaml is not installed. Run: pip install pyyaml")
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def print_registered_services():
    print_json_block("registered_services", build_service_summary())


def print_service_tasks(service_id=None):
    task_index = build_service_task_index()
    if service_id:
        if service_id not in task_index:
            raise RuntimeError(f"Unknown service: {service_id}")
        print_json_block(f"service_tasks:{service_id}", task_index[service_id])
        return
    print_json_block("service_tasks", task_index)


def print_service_tools(service_id):
    spec = get_service_spec(service_id)
    if spec is None:
        raise RuntimeError(f"Unknown service: {service_id}")

    backend_name = spec.default_backend or service_id
    if backend_name not in get_supported_backend_names():
        print_json_block(
            f"service_tools:{service_id}",
            {
                "service": spec.to_dict(),
                "tool_provider_registered": False,
                "tools": [],
            },
        )
        return

    backend = get_environment_backend() if get_pipeline_env() == backend_name else None
    if backend is None:
        from .environment import get_backend

        backend = get_backend(backend_name)

    print_json_block(
        f"service_tools:{service_id}",
        {
            "service": spec.to_dict(),
            "tool_provider_registered": True,
            "tools": backend.get_tool_summary(),
            "write_tools": backend.get_write_tool_names(),
        },
    )


def main():
    parser = argparse.ArgumentParser(description="Decision-driven safety pipeline")
    parser.add_argument("--task", help="Run a single task")
    parser.add_argument("--task-file", help="Load a task definition from a YAML file")
    parser.add_argument("--list-services", action="store_true", help="List registered services")
    parser.add_argument("--list-service-tasks", metavar="SERVICE_ID", help="List tasks under the specified service")
    parser.add_argument("--list-service-tools", metavar="SERVICE_ID", help="List tools registered under the specified service")
    parser.add_argument(
        "--env",
        choices=get_supported_backend_names(),
        default=get_pipeline_env(),
        help="Execution environment backend",
    )
    args = parser.parse_args()

    if args.list_services:
        print_registered_services()
        return

    if args.list_service_tasks:
        print_service_tasks(args.list_service_tasks)
        return

    if args.list_service_tools:
        print_service_tools(args.list_service_tools)
        return

    if args.env:
        set_pipeline_env(args.env)

    if args.task_file:
        task_config = load_task_file(args.task_file)
        if task_config.get("environment"):
            set_pipeline_env(task_config["environment"])
        pipeline(task_config["task"], task_config=task_config)
    elif args.task:
        pipeline(args.task)
    else:
        default_task = "Delete the stale-docs branch from the openclaw project"
        pipeline(default_task)


if __name__ == "__main__":
    main()
