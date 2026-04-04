#!/usr/bin/env python3
"""
Smoke test: run one representative task per service through the full pipeline + evaluation.

Usage:
    python scripts/smoke_test_all_services.py [--services gitea,discourse] [--timeout 120]
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from safety_pipeline.evaluation import (
    TaskEvaluator,
    check_tool_coverage,
    print_eval_result,
)
from safety_pipeline.environment import get_backend
from safety_pipeline.runtime import load_task_file, pipeline as run_pipeline
from safety_pipeline.settings import set_pipeline_env

# Representative read + rollback-path tasks per service.
SMOKE_TASKS = {
    "gitea": [
        "tasks/gitea/gitea-review-issue-thread.yaml",
        "tasks/gitea/gitea-force-push-main-terminate.yaml",
    ],
    "discourse": [
        "tasks/discourse/discourse-view-topic-posts.yaml",
        "tasks/discourse/discourse-close-downtime-thread-terminate.yaml",
    ],
    "zammad": [
        "tasks/zammad/zammad-view-ticket-history.yaml",
        "tasks/zammad/zammad-tag-invoice-urgent-terminate.yaml",
    ],
    "rocketchat": [
        "tasks/rocketchat/rocketchat-read-messages.yaml",
        "tasks/rocketchat/rocketchat-create-status-bridge-terminate.yaml",
    ],
    "owncloud": [
        "tasks/owncloud/owncloud-list-documents.yaml",
        "tasks/owncloud/owncloud-delete-api-rollout-folder-terminate.yaml",
    ],
    "mailu": [
        "tasks/mailu/mailu-list-users.yaml",
        "tasks/mailu/mailu-self-referencing-alias-terminate.yaml",
    ],
    "nocodb": [
        "tasks/nocodb/nocodb-list-databases.yaml",
        "tasks/nocodb/nocodb-delete-task-table-cleanup-terminate.yaml",
    ],
    "erpnext": [
        "tasks/erpnext/erpnext-list-companies.yaml",
        "tasks/erpnext/erpnext-record-large-acme-wire-terminate.yaml",
    ],
    "openemr": [
        "tasks/openemr/openemr-list-patients.yaml",
        "tasks/openemr/openemr-create-dr-patel-double-book-terminate.yaml",
    ],
}


def load_service_env(service_name):
    env_file = REPO_ROOT / f".env.{service_name}.generated"
    if not env_file.exists():
        return False
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip()
    return True


def run_single_task(task_path, timeout=180):
    task_config = load_task_file(task_path)
    env_name = task_config.get("environment", "gitea")
    service_name = task_config.get("service", env_name)

    load_service_env(service_name)
    set_pipeline_env(env_name)
    backend = get_backend(env_name)

    # Step 1: tool_coverage_check
    coverage = check_tool_coverage(task_config, backend=backend)
    if not coverage["passed"]:
        evaluator = TaskEvaluator(task_config, backend=backend)
        return evaluator.evaluate(coverage_check=coverage)

    # Step 2: reset environment
    backend.reset()

    # Step 3: run pipeline
    previous_noninteractive = os.environ.get("PIPELINE_NONINTERACTIVE")
    os.environ["PIPELINE_NONINTERACTIVE"] = "1"
    try:
        pipeline_result = run_pipeline(
            task_config["task"],
            npc_scenario=task_config.get("scenarios") or None,
            task_config=task_config,
        )
    finally:
        if previous_noninteractive is None:
            os.environ.pop("PIPELINE_NONINTERACTIVE", None)
        else:
            os.environ["PIPELINE_NONINTERACTIVE"] = previous_noninteractive

    # Step 4: evaluate
    evaluator = TaskEvaluator(task_config, backend=backend)
    return evaluator.evaluate(pipeline_result, coverage_check=coverage)


def main():
    parser = argparse.ArgumentParser(description="Smoke test all services")
    parser.add_argument("--services", help="Comma-separated service list (default: all)")
    parser.add_argument("--timeout", type=int, default=180, help="Timeout per task in seconds")
    parser.add_argument("--json-output", help="Write results to JSON file")
    args = parser.parse_args()

    if args.services:
        services = [s.strip() for s in args.services.split(",")]
    else:
        services = list(SMOKE_TASKS.keys())

    # Load base .env
    base_env = REPO_ROOT / ".env"
    if base_env.exists():
        with open(base_env) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())

    results = []
    summary = {"total": 0, "passed": 0, "failed": 0, "error": 0}

    for service in services:
        tasks = SMOKE_TASKS.get(service, [])
        if not tasks:
            print(f"\n[SKIP] {service}: no smoke tasks configured")
            continue

        for task_path in tasks:
            summary["total"] += 1
            task_id = Path(task_path).stem
            print(f"\n{'='*60}")
            print(f"[SMOKE] {service} / {task_id}")
            print(f"{'='*60}")

            start_time = time.time()
            try:
                result = run_single_task(task_path, timeout=args.timeout)
                elapsed = time.time() - start_time
                result["elapsed_seconds"] = round(elapsed, 1)
                result["service"] = service

                print_eval_result(result)

                if result["passed"]:
                    summary["passed"] += 1
                    print(f"  [TIME] {elapsed:.1f}s")
                else:
                    summary["failed"] += 1
                    print(f"  [TIME] {elapsed:.1f}s")

                results.append(result)

            except Exception as exc:
                elapsed = time.time() - start_time
                summary["error"] += 1
                error_result = {
                    "task_id": task_id,
                    "service": service,
                    "passed": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "elapsed_seconds": round(elapsed, 1),
                }
                results.append(error_result)
                print(f"  [ERROR] {type(exc).__name__}: {exc}")
                print(f"  [TIME] {elapsed:.1f}s")

    # Final summary
    print(f"\n{'='*60}")
    print(f"[SUMMARY] Total: {summary['total']}  Passed: {summary['passed']}  "
          f"Failed: {summary['failed']}  Error: {summary['error']}")
    print(f"{'='*60}")

    if args.json_output:
        output_path = Path(args.json_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump({"summary": summary, "results": results}, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n[OUTPUT] Results written to {args.json_output}")

    sys.exit(0 if summary["failed"] == 0 and summary["error"] == 0 else 1)


if __name__ == "__main__":
    main()
