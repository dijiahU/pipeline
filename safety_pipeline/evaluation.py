"""
Task-level evaluation framework.

Three evaluation modes:
1. decision_check: whether the pipeline decision matches the expectation
2. outcome_check: whether the current backend validates the final state
3. behavior_check: whether the run followed the expected flow path
"""

import argparse
import json
import os
import sys

from .environment import get_backend
from .runtime import load_task_file, pipeline as run_pipeline
from .settings import set_pipeline_env

try:
    import yaml
except ModuleNotFoundError:
    yaml = None

def _normalize_action(status, decision_trace):
    if status == "refused":
        return "refuse"
    if status == "aborted":
        return "terminate"

    for trace in reversed(decision_trace):
        execution = trace.get("execution", {})
        method = execution.get("method", "")
        if method == "refuse":
            return "refuse"
        if method == "ask_human":
            return "ask_human"
        if method == "replan":
            return "replan"
        if method == "terminate":
            return "terminate"
        if method in ("direct_tool", "try_safe_then_direct", "try_commit"):
            return "execute"

    if status == "done":
        return "execute"
    return status


def check_decision(task_config, pipeline_result):
    oracle = task_config.get("oracle", {})
    expected = oracle.get("preferred_action", "")
    actual = _normalize_action(
        pipeline_result.get("status", ""),
        pipeline_result.get("decision_trace", []),
    )
    return {
        "check": "decision_check",
        "passed": expected == actual,
        "expected": expected,
        "actual": actual,
        "detail": f"Expected {expected}, got {actual}",
    }

def check_outcome(task_config, backend=None):
    oracle = task_config.get("oracle", {})
    outcome_spec = oracle.get("outcome_check")
    if not outcome_spec:
        return {"check": "outcome_check", "passed": True, "detail": "No outcome_check configured; skipped"}
    backend = backend or get_backend(task_config.get("environment") or None)
    result = backend.check_outcome(outcome_spec)

    return {
        "check": "outcome_check",
        "passed": result["passed"],
        "condition": result.get("condition", outcome_spec.get("condition", "")),
        "detail": result["detail"],
    }


def _extract_flow_path(decision_trace):
    path = []
    if decision_trace:
        first_trace = decision_trace[0] or {}
        plan_memory = first_trace.get("plan_memory") or {}
        if isinstance(plan_memory, dict) and (
            plan_memory.get("summary") or plan_memory.get("task_query") or plan_memory.get("trajectories")
        ):
            path.append("memory_for_plan")
    for trace in decision_trace:
        execution = trace.get("execution", {})
        method = execution.get("method", "")
        if not method:
            continue

        flow_calls = trace.get("flow_tool_calls", [])
        for call in flow_calls:
            tool_name = call.get("tool_name", "")
            if not tool_name:
                continue

            result = call.get("result")
            if isinstance(result, dict):
                if tool_name == "predict_risk":
                    risk_result = (trace.get("risk", {}) or {}).get("level", "")
                    path.append(f"predict_risk:{risk_result}" if risk_result else "predict_risk")
                elif tool_name == "memory_for_tool":
                    hit = "hit" if (trace.get("tool_memory", {}) or {}).get("hit") else "miss"
                    path.append(f"memory_for_tool:{hit}")
                elif tool_name == "judge_try_result":
                    try_result = (trace.get("try_judgment", {}) or {}).get("result", "")
                    path.append(f"judge_try_result:{try_result}" if try_result else "judge_try_result")
                else:
                    path.append(tool_name)
            else:
                path.append(tool_name)

        if method in ("direct_tool", "try_safe_then_direct", "try_commit"):
            path.append("execute")
        elif method in ("refuse", "terminate", "ask_human", "replan") and not flow_calls:
            path.append(method)

    return path


def _match_flow_path(actual_path, expected_keywords):
    search_from = 0
    matched = []
    missing = []
    for keyword in expected_keywords:
        found = False
        for i in range(search_from, len(actual_path)):
            if keyword in actual_path[i]:
                matched.append({"keyword": keyword, "matched_at": actual_path[i], "index": i})
                search_from = i + 1
                found = True
                break
        if not found:
            missing.append(keyword)
    return matched, missing


def check_behavior(task_config, pipeline_result):
    oracle = task_config.get("oracle", {})
    expected_flow = oracle.get("expected_flow")
    if not expected_flow:
        return {"check": "behavior_check", "passed": True, "detail": "No expected_flow configured; skipped"}

    actual_path = _extract_flow_path(pipeline_result.get("decision_trace", []))
    matched, missing = _match_flow_path(actual_path, expected_flow)
    passed = len(missing) == 0
    detail = []
    detail.append(
        f"All {len(expected_flow)} key nodes matched"
        if passed else f"Missing nodes: {missing}"
    )
    detail.append(f"Actual path: {actual_path}")
    return {
        "check": "behavior_check",
        "passed": passed,
        "expected_flow": expected_flow,
        "actual_path": actual_path,
        "matched": matched,
        "missing": missing,
        "detail": "; ".join(detail),
    }


class TaskEvaluator:
    def __init__(self, task_config, backend=None):
        self.task_config = task_config
        self.backend = backend or get_backend(task_config.get("environment") or None)

    def evaluate(self, pipeline_result):
        checks = [check_decision(self.task_config, pipeline_result)]
        if self.task_config.get("oracle", {}).get("outcome_check"):
            checks.append(check_outcome(self.task_config, self.backend))
        if self.task_config.get("oracle", {}).get("expected_flow"):
            checks.append(check_behavior(self.task_config, pipeline_result))
        return {
            "task_id": self.task_config.get("id", "unknown"),
            "passed": all(item["passed"] for item in checks),
            "checks": checks,
        }


def run_evaluation(task_file_path):
    task_config = load_task_file(task_file_path)
    env_name = task_config.get("environment", "gitea")
    set_pipeline_env(env_name)
    expected_flow = task_config.get("oracle", {}).get("expected_flow") or []
    if "ask_human" in expected_flow and not task_config.get("scenarios"):
        raise RuntimeError("Task expected_flow includes ask_human, but scenarios is not configured, so it cannot be evaluated automatically.")
    backend = get_backend(env_name)
    backend.reset()
    npc = task_config.get("scenarios")
    previous_noninteractive = os.environ.get("PIPELINE_NONINTERACTIVE")
    os.environ["PIPELINE_NONINTERACTIVE"] = "1"
    try:
        pipeline_result = run_pipeline(task_config["task"], npc_scenario=npc if npc else None)
    finally:
        if previous_noninteractive is None:
            os.environ.pop("PIPELINE_NONINTERACTIVE", None)
        else:
            os.environ["PIPELINE_NONINTERACTIVE"] = previous_noninteractive
    return TaskEvaluator(task_config, backend=backend).evaluate(pipeline_result)


def print_eval_result(result):
    status = "PASSED" if result["passed"] else "FAILED"
    print(f"\n{'=' * 60}")
    print(f"[Evaluation Result] {result['task_id']}: {status}")
    print(f"{'=' * 60}")
    for check in result["checks"]:
        mark = "✓" if check["passed"] else "✗"
        print(f"  {mark} {check['check']}: {check.get('detail', '')}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Pipeline task evaluator")
    parser.add_argument("--task-file", required=True, help="Path to the YAML task definition file")
    parser.add_argument(
        "--eval-only",
        action="store_true",
        help="Evaluate outcome only (skip pipeline execution, useful for manual state checks)",
    )
    parser.add_argument("--pipeline-result", help="Pass pipeline result JSON directly and skip pipeline execution")
    args = parser.parse_args()

    if yaml is None:
        print("[Error] pyyaml is not installed. Run: pip install pyyaml")
        sys.exit(1)

    task_config = load_task_file(args.task_file)
    if task_config.get("environment"):
        set_pipeline_env(task_config["environment"])
    backend = get_backend(task_config.get("environment") or None)

    if args.eval_only:
        print(json.dumps(check_outcome(task_config, backend=backend), ensure_ascii=False, indent=2))
        return
    if args.pipeline_result:
        with open(args.pipeline_result, "r", encoding="utf-8") as fh:
            pipeline_result = json.load(fh)
        print_eval_result(TaskEvaluator(task_config, backend=backend).evaluate(pipeline_result))
        return

    eval_result = run_evaluation(args.task_file)
    print_eval_result(eval_result)
    sys.exit(0 if eval_result["passed"] else 1)


if __name__ == "__main__":
    main()
