#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_ROOT = REPO_ROOT / "tasks"
REPORTS_ROOT = REPO_ROOT / "memory" / "task_suite_reports"


def discover_task_files(service_id):
    service_dir = TASKS_ROOT / service_id
    if not service_dir.is_dir():
        raise SystemExit(f"[error] unknown service task directory: {service_dir}")
    return sorted(service_dir.glob("*.yaml"))


def run_task(task_file, python_executable, extra_args):
    fd, json_output_path = tempfile.mkstemp(prefix="suite_eval_", suffix=".json")
    os.close(fd)
    cmd = [
        python_executable,
        "-m",
        "safety_pipeline.evaluation",
        "--task-file",
        str(task_file),
        "--json-output",
        json_output_path,
    ] + list(extra_args)
    started = time.time()
    result = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    elapsed = time.time() - started
    eval_result = None
    try:
        with open(json_output_path, "r", encoding="utf-8") as fh:
            eval_result = json.load(fh)
    except FileNotFoundError:
        eval_result = None
    except json.JSONDecodeError:
        eval_result = None
    finally:
        try:
            os.remove(json_output_path)
        except FileNotFoundError:
            pass
    return {
        "task_file": task_file,
        "cmd": cmd,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "elapsed_seconds": elapsed,
        "eval_result": eval_result,
    }


def _build_check_status_map(eval_result):
    checks = {}
    for item in (eval_result or {}).get("checks", []):
        name = item.get("check")
        if name:
            checks[name] = item.get("passed")
    return checks


def _quality_entry(result):
    eval_result = result.get("eval_result") or {}
    quality_tier = eval_result.get("quality_tier")
    quality_reasons = list(eval_result.get("quality_reasons") or [])
    if not quality_tier:
        quality_tier = "unverified"
        if not quality_reasons:
            quality_reasons = ["missing_structured_eval_result"]

    return {
        "task_file": str(result["task_file"].relative_to(REPO_ROOT)),
        "task_id": eval_result.get("task_id", ""),
        "quality_tier": quality_tier,
        "quality_reasons": quality_reasons,
        "passed": bool(eval_result.get("passed")) if eval_result else False,
        "checks": _build_check_status_map(eval_result),
        "actual_path": list(eval_result.get("actual_path") or []),
        "elapsed_seconds": round(result["elapsed_seconds"], 3),
        "returncode": result["returncode"],
    }


def _write_suite_report(service, mode, stop_on_fail, results):
    REPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = REPORTS_ROOT / f"{service}_{timestamp}.json"

    quality_counts = {}
    quality_tasks = {
        "gold": [],
        "silver": [],
        "drop": [],
        "unverified": [],
    }
    failed_tasks = []

    for result in results:
        entry = _quality_entry(result)
        tier = entry["quality_tier"]
        quality_counts[tier] = quality_counts.get(tier, 0) + 1
        quality_tasks.setdefault(tier, []).append(entry)
        if result["returncode"] != 0:
            failed_tasks.append(entry["task_file"])

    report = {
        "service": service,
        "mode": mode,
        "stop_on_fail": stop_on_fail,
        "generated_at": timestamp,
        "task_count": len(results),
        "passed_count": sum(1 for item in results if item["returncode"] == 0),
        "failed_count": sum(1 for item in results if item["returncode"] != 0),
        "quality_counts": quality_counts,
        "silver_tasks": quality_tasks.get("silver", []),
        "drop_tasks": quality_tasks.get("drop", []),
        "unverified_tasks": quality_tasks.get("unverified", []),
        "failed_tasks": failed_tasks,
    }

    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    return report_path, report


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
    completed = []

    for index, task_file in enumerate(task_files, start=1):
        rel_task = task_file.relative_to(REPO_ROOT)
        print(f"\n[{index}/{len(task_files)}] {rel_task}")
        result = run_task(task_file, args.python, extra_args)
        completed.append(result)
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

    report_path, report = _write_suite_report(
        args.service,
        "eval-only" if args.eval_only else "full-eval",
        args.stop_on_fail,
        completed,
    )

    print("\n[suite-summary]")
    print(f"service={args.service}")
    print(f"passed={len(passed)}")
    print(f"failed={len(failed)}")
    print(f"total={len(passed) + len(failed)}")
    print("[quality-summary]")
    print(f"gold={report['quality_counts'].get('gold', 0)}")
    print(f"silver={report['quality_counts'].get('silver', 0)}")
    print(f"drop={report['quality_counts'].get('drop', 0)}")
    print(f"unverified={report['quality_counts'].get('unverified', 0)}")
    print(f"[quality-report] {report_path}")

    if report["silver_tasks"]:
        print("[silver-tasks]")
        for item in report["silver_tasks"]:
            reasons = ", ".join(item["quality_reasons"]) if item["quality_reasons"] else "n/a"
            print(f"{item['task_file']} :: {reasons}")

    if report["drop_tasks"]:
        print("[drop-tasks]")
        for item in report["drop_tasks"]:
            reasons = ", ".join(item["quality_reasons"]) if item["quality_reasons"] else "n/a"
            print(f"{item['task_file']} :: {reasons}")

    if failed:
        print("[failed-tasks]")
        for item in failed:
            print(item["task_file"].relative_to(REPO_ROOT))
        sys.exit(1)


if __name__ == "__main__":
    main()
