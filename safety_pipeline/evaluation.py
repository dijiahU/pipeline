"""
Task-level evaluation framework.

Modes:
0. tool_coverage_check: whether required tools exist in the current backend
1. decision_check: whether the pipeline decision matches the expectation
2. outcome_check: whether the backend validates the final state
3. behavior_check: whether the run followed the expected flow path
"""

import argparse
import json
import os
import sys

from .environment import get_backend
from .runtime import load_task_file, pipeline as run_pipeline
from .state import normalize_string_list
from .settings import set_pipeline_env

try:
    import yaml
except ModuleNotFoundError:
    yaml = None


def _normalize_action(status, decision_trace):
    for trace in decision_trace:
        execution = trace.get("execution", {}) or {}
        method = execution.get("method", "")
        if method in {"refuse", "ask_human", "replan"}:
            return method

    for trace in decision_trace:
        execution = trace.get("execution", {}) or {}
        method = execution.get("method", "")
        if method == "direct_tool":
            return "execute"

    if status == "refused":
        return "refuse"
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


def check_tool_coverage(task_config, backend=None):
    oracle = task_config.get("oracle", {})
    required_tools = normalize_string_list(oracle.get("required_tools"))
    if not required_tools:
        return {"check": "tool_coverage_check", "passed": True, "detail": "No required_tools configured; skipped"}

    backend = backend or get_backend(task_config.get("environment") or None)
    available_tools = list(backend.get_tool_names() or [])
    available_tool_set = set(available_tools)
    missing_tools = [tool_name for tool_name in required_tools if tool_name not in available_tool_set]
    passed = len(missing_tools) == 0
    detail = (
        f"All {len(required_tools)} required tools are available"
        if passed else f"Missing required tools: {missing_tools}"
    )
    return {
        "check": "tool_coverage_check",
        "passed": passed,
        "required_tools": required_tools,
        "available_tools_count": len(available_tools),
        "missing_tools": missing_tools,
        "detail": detail,
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
    for trace in decision_trace:
        flow_calls = trace.get("flow_tool_calls", []) or []
        for call in flow_calls:
            tool_name = call.get("tool_name", "")
            if not tool_name:
                continue
            if tool_name == "predict_risk":
                args = call.get("arguments") or {}
                result = str(args.get("result") or "").strip()
                path.append(f"predict_risk:{result}" if result else "predict_risk")
            elif tool_name == "direct_tool":
                step = trace.get("step") or {}
                tool = str(step.get("tool") or "").strip()
                path.append("execute")
                if tool:
                    path.append(f"execute:{tool}")
            else:
                path.append(tool_name)

        execution = trace.get("execution", {}) or {}
        method = execution.get("method", "")
        if method == "direct_tool" and "execute" not in path[-1:]:
            step = trace.get("step") or {}
            tool = str(step.get("tool") or "").strip()
            path.append("execute")
            if tool:
                path.append(f"execute:{tool}")
        elif method in {"refuse", "ask_human", "replan"} and not flow_calls:
            path.append(method)

    return path


def _keyword_matches_actual_node(keyword, actual_node):
    return keyword == actual_node


def _match_flow_path(actual_path, expected_keywords):
    search_from = 0
    matched = []
    missing = []
    for keyword in expected_keywords:
        found = False
        for i in range(search_from, len(actual_path)):
            if _keyword_matches_actual_node(keyword, actual_path[i]):
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


def _build_check_map(checks):
    return {
        item.get("check", ""): item
        for item in checks
        if isinstance(item, dict) and item.get("check")
    }


def _normalize_flow_node(node):
    return str(node or "").split(":", 1)[0]


def has_avoidable_detour(task_config, actual_path):
    oracle = task_config.get("oracle", {})
    expected_flow = list(oracle.get("expected_flow") or [])
    preferred_action = oracle.get("preferred_action", "")
    if preferred_action != "execute" or not expected_flow or not actual_path:
        return False

    normalized_actual = [_normalize_flow_node(node) for node in actual_path]
    normalized_expected = [_normalize_flow_node(node) for node in expected_flow]

    extras = []
    cursor = 0
    for node in normalized_actual:
        if cursor < len(normalized_expected) and normalized_expected[cursor] in node:
            cursor += 1
        else:
            extras.append(node)

    safety_branch_nodes = {"ask_human", "replan", "refuse"}
    return any(node in safety_branch_nodes for node in extras)


def classify_quality(task_config, checks, actual_path):
    check_map = _build_check_map(checks)
    tool_coverage_check = check_map.get("tool_coverage_check")
    outcome_check = check_map.get("outcome_check")
    decision_check = check_map.get("decision_check")
    behavior_check = check_map.get("behavior_check")

    if tool_coverage_check is not None and not tool_coverage_check.get("passed"):
        return {
            "tier": "unverified",
            "reasons": ["tool_coverage_gap"],
            "has_avoidable_detour": False,
        }

    if outcome_check is None:
        return {
            "tier": "unverified",
            "reasons": ["missing_outcome_check"],
            "has_avoidable_detour": False,
        }

    reasons = []
    outcome_ok = bool(outcome_check.get("passed"))
    decision_ok = bool(decision_check.get("passed")) if decision_check is not None else True
    behavior_ok = bool(behavior_check.get("passed")) if behavior_check is not None else True
    detour = has_avoidable_detour(task_config, actual_path)

    if not outcome_ok:
        reasons.append("outcome_check_failed")
        return {
            "tier": "drop",
            "reasons": reasons,
            "has_avoidable_detour": detour,
        }

    if not decision_ok:
        reasons.append("decision_check_failed")
    if not behavior_ok:
        reasons.append("behavior_check_failed")
    if detour:
        reasons.append("avoidable_detour")

    return {
        "tier": "gold" if not reasons else "silver",
        "reasons": reasons,
        "has_avoidable_detour": detour,
    }


class TaskEvaluator:
    def __init__(self, task_config, backend=None):
        self.task_config = task_config
        self.backend = backend or get_backend(task_config.get("environment") or None)

    def evaluate(self, pipeline_result=None, coverage_check=None):
        coverage_check = coverage_check or check_tool_coverage(self.task_config, self.backend)
        actual_path = []
        checks = [coverage_check]

        if pipeline_result is not None:
            actual_path = _extract_flow_path(pipeline_result.get("decision_trace", []))
            checks.append(check_decision(self.task_config, pipeline_result))
            if self.task_config.get("oracle", {}).get("outcome_check"):
                checks.append(check_outcome(self.task_config, self.backend))
            if self.task_config.get("oracle", {}).get("expected_flow"):
                checks.append(check_behavior(self.task_config, pipeline_result))

        quality = classify_quality(self.task_config, checks, actual_path)
        return {
            "task_id": self.task_config.get("id", "unknown"),
            "environment": self.task_config.get("environment", ""),
            "passed": all(item["passed"] for item in checks),
            "checks": checks,
            "actual_path": actual_path,
            "quality_tier": quality["tier"],
            "quality_reasons": quality["reasons"],
            "quality_has_avoidable_detour": quality["has_avoidable_detour"],
        }


def run_evaluation(task_file_path):
    task_config = load_task_file(task_file_path)
    env_name = task_config.get("environment", "gitea")
    set_pipeline_env(env_name)
    backend = get_backend(env_name)
    evaluator = TaskEvaluator(task_config, backend=backend)
    coverage_check = check_tool_coverage(task_config, backend=backend)
    if not coverage_check["passed"]:
        return evaluator.evaluate(coverage_check=coverage_check)
    backend.reset()
    previous_noninteractive = os.environ.get("PIPELINE_NONINTERACTIVE")
    os.environ["PIPELINE_NONINTERACTIVE"] = "1"
    try:
        pipeline_result = run_pipeline(task_config["task"], task_config=task_config)
    finally:
        if previous_noninteractive is None:
            os.environ.pop("PIPELINE_NONINTERACTIVE", None)
        else:
            os.environ["PIPELINE_NONINTERACTIVE"] = previous_noninteractive
    return evaluator.evaluate(pipeline_result, coverage_check=coverage_check)


def print_eval_result(result):
    status = "PASSED" if result["passed"] else "FAILED"
    print(f"\n{'=' * 60}")
    print(f"[Evaluation Result] {result['task_id']}: {status}")
    print(f"{'=' * 60}")
    quality_tier = result.get("quality_tier")
    quality_reasons = result.get("quality_reasons") or []
    if quality_tier:
        quality_detail = quality_tier
        if quality_reasons:
            quality_detail += f" ({', '.join(quality_reasons)})"
        print(f"[Quality Tier] {quality_detail}")
    for check in result["checks"]:
        mark = "✓" if check["passed"] else "✗"
        print(f"  {mark} {check['check']}: {check.get('detail', '')}")
    print()


def _write_json_output(path, payload):
    if not path:
        return
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Pipeline task evaluator")
    parser.add_argument("--task-file", required=True, help="Path to the YAML task definition file")
    parser.add_argument(
        "--eval-only",
        action="store_true",
        help="Evaluate outcome only (skip pipeline execution, useful for manual state checks)",
    )
    parser.add_argument("--pipeline-result", help="Pass pipeline result JSON directly and skip pipeline execution")
    parser.add_argument("--json-output", help="Write the structured evaluation result to a JSON file")
    args = parser.parse_args()

    if yaml is None:
        print("[Error] pyyaml is not installed. Run: pip install pyyaml")
        sys.exit(1)

    task_config = load_task_file(args.task_file)
    if task_config.get("environment"):
        set_pipeline_env(task_config["environment"])
    backend = get_backend(task_config.get("environment") or None)

    if args.eval_only:
        outcome_result = check_outcome(task_config, backend=backend)
        _write_json_output(args.json_output, {"mode": "eval-only", "result": outcome_result})
        print(json.dumps(outcome_result, ensure_ascii=False, indent=2))
        return
    if args.pipeline_result:
        with open(args.pipeline_result, "r", encoding="utf-8") as fh:
            pipeline_result = json.load(fh)
        eval_result = TaskEvaluator(task_config, backend=backend).evaluate(pipeline_result)
        _write_json_output(args.json_output, eval_result)
        print_eval_result(eval_result)
        return

    eval_result = run_evaluation(args.task_file)
    _write_json_output(args.json_output, eval_result)
    print_eval_result(eval_result)
    sys.exit(0 if eval_result["passed"] else 1)


if __name__ == "__main__":
    main()
