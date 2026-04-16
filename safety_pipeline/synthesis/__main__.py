import argparse
import json
import os

from ..session_store import append_session_record
from ..settings import TRACE_SESSION_PATH, set_pipeline_env
from ..task_catalog import iter_task_files
from ..runtime import export_decision_token_sft, load_task_file
from .pass1_runner import run_task_pure
from .pass2_reviewer import review_trace
from .trajectory_writer import splice


def _append_session_cases(session_cases):
    append_session_record(session_cases)


def _run_one(task_file):
    task_config = load_task_file(task_file)
    if task_config.get("environment"):
        set_pipeline_env(task_config["environment"])
    pass1_trace = run_task_pure(task_config)
    pass2_decisions = review_trace(pass1_trace)
    synthetic_trace = splice(pass1_trace, pass2_decisions, task_config)
    _append_session_cases(synthetic_trace.get("session_cases", []))
    return synthetic_trace


def main():
    parser = argparse.ArgumentParser(description="Two-pass synthetic trace generator")
    parser.add_argument("--task-file", help="Path to one YAML task definition")
    parser.add_argument("--out", help="Optional JSONL output path for synthetic traces")
    args = parser.parse_args()

    task_files = [args.task_file] if args.task_file else list(iter_task_files())
    traces = []
    for task_file in task_files:
        trace = _run_one(task_file)
        traces.append(trace)
        print(json.dumps({"task_file": task_file, "final_status": trace.get("final_status", "")}, ensure_ascii=False))

    decision_export = export_decision_token_sft(verbose=False)

    if args.out:
        output_dir = os.path.dirname(args.out)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            for trace in traces:
                fh.write(json.dumps(trace, ensure_ascii=False) + "\n")

    print(
        json.dumps(
            {
                "trace_sessions": TRACE_SESSION_PATH,
                "trace_count": len(traces),
                "decision_token_sft": decision_export.get("output_path", ""),
                "decision_token_count": decision_export.get("count", 0),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
