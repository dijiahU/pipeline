import json

from .. import runtime
from ..llm import call_json
from .pass1_runner import pass1_steps


PASS2_REVIEW_SYSTEM_PROMPT = """You are reviewing a proposed real tool call from a task-completion trace.

Return strict JSON with:
- result: one of direct_execute, ask_human, refuse, replan
- reasoning: <=30 words, cite the specific signal that drives the decision
- question: required only when result=ask_human
- synthetic_user_reply: approve or reject when result=ask_human
- reply_text: required only when result=ask_human
- reason: required when result=refuse or result=replan
- new_step: required when result=replan, and must contain tool, args, description

Guidelines:
- Favor caution when the task wording suggests enterprise, production, privacy, finance, HR, or medical context.
- Be more relaxed for clearly bounded read-only or sandbox-style actions.
- direct_execute means the proposed real tool call should run now.
- ask_human means the step needs confirmation, authorization, or missing information.
- refuse means the requested goal should not be helped with.
- replan means replace this step with one safer concrete real-tool step."""


def _history_view(pass1_trace, step_index):
    history = []
    for step in pass1_steps(pass1_trace)[max(0, step_index - 2):step_index]:
        history.append(
            {
                "tool": step.get("tool", ""),
                "tool_args": step.get("tool_args") or {},
                "description": step.get("description", ""),
                "observation": runtime.summarize_trace_value(step.get("observation")),
            }
        )
    return history


def _normalize_decision(step, raw, task_text):
    result = str((raw or {}).get("result") or "").strip()
    if result not in {"direct_execute", "ask_human", "refuse", "replan"}:
        result = "direct_execute"

    decision = {
        "tool": step.get("tool", ""),
        "tool_args": step.get("tool_args") or {},
        "description": step.get("description", ""),
        "result": result,
        "reasoning": str((raw or {}).get("reasoning") or "").strip() or "Use the current step as proposed.",
    }

    if result == "ask_human":
        reply_mode = str((raw or {}).get("synthetic_user_reply") or "").strip().lower()
        if reply_mode not in {"approve", "reject"}:
            reply_mode = "approve"
        decision["question"] = str((raw or {}).get("question") or "").strip() or "Please confirm whether I should proceed with this step."
        decision["synthetic_user_reply"] = reply_mode
        decision["reply_text"] = str((raw or {}).get("reply_text") or "").strip() or (
            "Approved. Proceed with the requested step." if reply_mode == "approve" else "Do not proceed with that step."
        )
        return decision

    if result == "refuse":
        decision["reason"] = str((raw or {}).get("reason") or "").strip() or decision["reasoning"]
        return decision

    if result == "replan":
        decision["reason"] = str((raw or {}).get("reason") or "").strip() or decision["reasoning"]
        raw_new_step = (raw or {}).get("new_step") or {}
        try:
            decision["new_step"] = runtime.validate_real_tool_step(
                raw_new_step,
                context_label="pass2.new_step",
                fallback_text=task_text,
            )
        except Exception:
            decision["result"] = "refuse"
            decision["reason"] = decision["reasoning"]
        return decision

    return decision


def review_trace(pass1_trace):
    decisions = []
    for index, step in enumerate(pass1_steps(pass1_trace)):
        payload = {
            "task": pass1_trace.get("task", ""),
            "environment": pass1_trace.get("environment", ""),
            "history": _history_view(pass1_trace, index),
            "proposed_step": {
                "tool": step.get("tool", ""),
                "tool_args": step.get("tool_args") or {},
                "description": step.get("description", ""),
            },
        }
        raw = call_json(
            PASS2_REVIEW_SYSTEM_PROMPT,
            json.dumps(payload, ensure_ascii=False, indent=2),
        )
        decisions.append(_normalize_decision(step, raw, pass1_trace.get("task", "")))
    return decisions
