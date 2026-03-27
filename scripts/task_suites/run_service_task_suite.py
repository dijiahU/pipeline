#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_ROOT = REPO_ROOT / "tasks"


def discover_task_files(service_id):
    service_dir = TASKS_ROOT / service_id
    if not service_dir.is_dir():
        raise SystemExit(f"[error] unknown service task directory: {service_dir}")
    return sorted(service_dir.glob("*.yaml"))


def run_task(task_file, python_executable, extra_args):
    cmd = [
        python_executable,
        "-m",
        "safety_pipeline.evaluation",
        "--task-file",
        str(task_file),
    ] + list(extra_args)
    started = time.time()
    result = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    elapsed = time.time() - started
    return {
        "task_file": task_file,
        "cmd": cmd,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "elapsed_seconds": elapsed,
    }


def main():
    parser = argparse.ArgumentParser(description="Run all task evaluations for a service.")
    parser.add_argument("service", help="service id, e.g. gitea/openemr/zammad")
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable used to run safety_pipeline.evaluation",
    )
    parser.add_argument(
        "--eval-only",
        action="store_true",
        help="Pass --eval-only to the evaluator instead of running the full pipeline",
    )
    parser.add_argument(
        "--stop-on-fail",
        action="store_true",
        help="Stop immediately on the first failed task",
    )
    args = parser.parse_args()

    task_files = discover_task_files(args.service)
    if not task_files:
        raise SystemExit(f"[error] no task files found for service: {args.service}")

    extra_args = ["--eval-only"] if args.eval_only else []
    print(f"[suite] service={args.service}")
    print(f"[suite] task_count={len(task_files)}")
    print(f"[suite] mode={'eval-only' if args.eval_only else 'full-eval'}")

    passed = []
    failed = []

    for index, task_file in enumerate(task_files, start=1):
        rel_task = task_file.relative_to(REPO_ROOT)
        print(f"\n[{index}/{len(task_files)}] {rel_task}")
        result = run_task(task_file, args.python, extra_args)
        if result["stdout"]:
            print(result["stdout"], end="" if result["stdout"].endswith("\n") else "\n")
        if result["stderr"]:
            print(result["stderr"], end="" if result["stderr"].endswith("\n") else "\n", file=sys.stderr)

        if result["returncode"] == 0:
            passed.append(result)
            print(f"[result] PASS ({result['elapsed_seconds']:.1f}s)")
        else:
            failed.append(result)
            print(f"[result] FAIL ({result['elapsed_seconds']:.1f}s)")
            if args.stop_on_fail:
                break

    print("\n[suite-summary]")
    print(f"service={args.service}")
    print(f"passed={len(passed)}")
    print(f"failed={len(failed)}")
    print(f"total={len(passed) + len(failed)}")

    if failed:
        print("[failed-tasks]")
        for item in failed:
            print(item["task_file"].relative_to(REPO_ROOT))
        sys.exit(1)


if __name__ == "__main__":
    main()
