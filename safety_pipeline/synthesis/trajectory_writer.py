import copy

from .. import runtime
from ..state import build_dialogue_context_snapshot
from .pass1_runner import run_task_pure
from .pass2_reviewer import review_trace


def _service_context():
    return runtime.build_runtime_service_context()


def _predict_call(decision, next_phase):
    return {
        "tool_name": "predict_risk",
        "arguments": {
            "tool": decision.get("tool", ""),
            "tool_args": decision.get("tool_args") or {},
            "description": decision.get("description", ""),
            "result": decision.get("result", ""),
            "reasoning": decision.get("reasoning", ""),
        },
        "result": {
            "accepted": True,
            "stored_as": "current_risk_assessment",
            "next_phase": next_phase,
        },
    }


def _base_case(task_text, state_before, step_index, step, flow_tool_calls, decision, outcome, *, decision_reason="", observed_result=None, final_reply=""):
    service_context = _service_context()
    case = {
        "task": task_text,
        "turn_id": state_before.get("turn_count", 1),
        "step_index": step_index,
        "dialogue_snapshot": build_dialogue_context_snapshot(state_before),
        "flow_tool_calls": flow_tool_calls,
        "step": step,
        "decision": decision,
        "outcome": outcome,
        "service_id": service_context.get("service_id", ""),
        "environment": service_context.get("environment", ""),
    }
    if flow_tool_calls and flow_tool_calls[0].get("tool_name") == "predict_risk":
        predict_args = flow_tool_calls[0].get("arguments") or {}
        case["risk"] = {
            "result": predict_args.get("result", ""),
            "reasoning": predict_args.get("reasoning", ""),
        }
    if decision_reason:
        case["decision_reason"] = decision_reason
    if observed_result is not None:
        case["observed_result"] = observed_result
    if final_reply:
        case["final_reply"] = final_reply
    return case


def _dialogue_snapshot_with_reply(state_before, question, reply_text):
    state_copy = copy.deepcopy(state_before)
    state_copy.setdefault("dialogue_history", [])
    state_copy["dialogue_history"].append({"role": "assistant", "content": question})
    state_copy["dialogue_history"].append({"role": "user", "content": reply_text})
    return build_dialogue_context_snapshot(state_copy)


def _case_to_trace(case):
    return {
        "turn_id": case.get("turn_id", 1),
        "step_index": case.get("step_index", 0),
        "step": case.get("step") or {},
        "flow_tool_calls": case.get("flow_tool_calls") or [],
        "risk": runtime.compact_risk_record(case.get("risk")),
        "execution": {
            "method": case.get("decision", ""),
            "result": case.get("observed_result"),
        },
    }


def _session_to_pipeline_result(session_cases, final_status, final_response):
    results = []
    for case in session_cases:
        if case.get("decision") != "direct_tool":
            continue
        step = case.get("step") or {}
        results.append(
            {
                "tool": step.get("tool", ""),
                "args": step.get("args") or {},
                "result": case.get("observed_result"),
                "method": "direct_tool",
            }
        )

    status = final_status
    if final_status in {"refused", "refused_after_ask"}:
        status = "refused"
    elif final_status == "done":
        status = "done"

    return {
        "status": status,
        "results": results,
        "decision_trace": [_case_to_trace(case) for case in session_cases],
        "final_response": final_response,
        "session_cases": session_cases,
        "final_status": final_status,
    }


def splice(pass1_trace, pass2_decisions, task_config, replan_depth=0):
    session_cases = []
    final_status = pass1_trace.get("final_status", "done")
    final_response = pass1_trace.get("final_response", "")

    for step, decision in zip(pass1_trace.get("steps", []), pass2_decisions):
        state_before = copy.deepcopy(step.get("state_snapshot_at_step") or {})
        real_step = {
            "tool": step.get("tool", ""),
            "args": step.get("tool_args") or {},
            "description": step.get("description", ""),
        }

        if decision["result"] == "direct_execute":
            calls = [
                _predict_call(decision, "need_next_or_done"),
                {
                    "tool_name": "direct_tool",
                    "arguments": {},
                    "result": {
                        "tool": real_step["tool"],
                        "tool_args": real_step["args"],
                        "exec_result": step.get("observation"),
                    },
                },
            ]
            session_cases.append(
                _base_case(
                    pass1_trace["task"],
                    state_before,
                    len(session_cases),
                    real_step,
                    calls,
                    "direct_tool",
                    "executed",
                    observed_result=step.get("observation"),
                )
            )
            continue

        if decision["result"] == "ask_human":
            question = decision["question"]
            reply_text = decision["reply_text"]
            dialogue_snapshot = _dialogue_snapshot_with_reply(state_before, question, reply_text)
            ask_calls = [
                _predict_call(decision, "need_action_branch"),
                {
                    "tool_name": "ask_human",
                    "arguments": {"question": question},
                    "result": {"status": "updated"},
                },
            ]
            ask_case = _base_case(
                pass1_trace["task"],
                state_before,
                len(session_cases),
                real_step,
                ask_calls,
                "ask_human",
                "ask_human_feedback",
                decision_reason=question,
                observed_result={"human_reply": reply_text},
            )
            ask_case["dialogue_snapshot"] = dialogue_snapshot
            session_cases.append(ask_case)

            if decision["synthetic_user_reply"] == "reject":
                final_status = "refused_after_ask"
                final_response = reply_text
                break

            execute_decision = dict(decision)
            execute_decision["result"] = "direct_execute"
            exec_calls = [
                _predict_call(execute_decision, "need_next_or_done"),
                {
                    "tool_name": "direct_tool",
                    "arguments": {},
                    "result": {
                        "tool": real_step["tool"],
                        "tool_args": real_step["args"],
                        "exec_result": step.get("observation"),
                    },
                },
            ]
            exec_case = _base_case(
                pass1_trace["task"],
                state_before,
                len(session_cases),
                real_step,
                exec_calls,
                "direct_tool",
                "executed",
                observed_result=step.get("observation"),
            )
            exec_case["dialogue_snapshot"] = dialogue_snapshot
            session_cases.append(exec_case)
            continue

        if decision["result"] == "refuse":
            calls = [
                _predict_call(decision, "need_action_branch"),
                {
                    "tool_name": "refuse",
                    "arguments": {"reason": decision["reason"]},
                    "result": {"status": "refused"},
                },
            ]
            session_cases.append(
                _base_case(
                    pass1_trace["task"],
                    state_before,
                    len(session_cases),
                    real_step,
                    calls,
                    "refuse",
                    "refused",
                    decision_reason=decision["reason"],
                    observed_result={"reason": decision["reason"]},
                )
            )
            final_status = "refused"
            final_response = decision["reason"]
            break

        if decision["result"] == "replan":
            if replan_depth >= 2:
                refusal = dict(decision)
                refusal["result"] = "refuse"
                refusal["reason"] = decision.get("reason", "") or decision.get("reasoning", "")
                calls = [
                    _predict_call(refusal, "need_action_branch"),
                    {
                        "tool_name": "refuse",
                        "arguments": {"reason": refusal["reason"]},
                        "result": {"status": "refused"},
                    },
                ]
                session_cases.append(
                    _base_case(
                        pass1_trace["task"],
                        state_before,
                        len(session_cases),
                        real_step,
                        calls,
                        "refuse",
                        "refused",
                        decision_reason=refusal["reason"],
                        observed_result={"reason": refusal["reason"]},
                    )
                )
                final_status = "refused"
                final_response = refusal["reason"]
                break

            new_step = decision["new_step"]
            calls = [
                _predict_call(decision, "need_action_branch"),
                {
                    "tool_name": "replan",
                    "arguments": {"reasoning": decision["reason"], "new_step": new_step},
                    "result": {"accepted": True, "new_step_count": 1},
                },
            ]
            session_cases.append(
                _base_case(
                    pass1_trace["task"],
                    state_before,
                    len(session_cases),
                    real_step,
                    calls,
                    "replan",
                    "replanned_step",
                    decision_reason=decision["reason"],
                    observed_result=new_step,
                )
            )
            rerun_trace = run_task_pure(task_config, initial_state=state_before, injected_first_step=new_step)
            rerun_decisions = review_trace(rerun_trace)
            rerun_result = splice(rerun_trace, rerun_decisions, task_config, replan_depth=replan_depth + 1)
            for case in rerun_result["session_cases"]:
                case = copy.deepcopy(case)
                case["step_index"] = len(session_cases)
                session_cases.append(case)
            final_status = rerun_result["final_status"]
            final_response = rerun_result.get("final_response", "")
            break

    if session_cases and final_status == "done":
        session_cases[-1]["final_reply"] = final_response

    result = _session_to_pipeline_result(session_cases, final_status, final_response)
    result["task"] = pass1_trace["task"]
    result["environment"] = pass1_trace.get("environment", "")
    return result
