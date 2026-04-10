"""Generate correct ask_human trajectories using GPT-5.4 for SFT training data."""

import json
import os
import sys

from config import MODELS, RESULTS_DIR, TRACES_PATH
from runner import run_task
from tasks import load_ask_human_tasks, split_train_test


def generate_training_traces(
    train_tasks: list[dict],
    model_config: dict,
    prompt_variant: str = "explicit_rules",
    output_path: str | None = None,
) -> tuple[int, int]:
    """Generate traces for training tasks with the teacher model.

    Uses Prompt B (explicit_rules) to maximize correct ask_human behavior.
    Only keeps traces where the model ultimately chose ask_human and the final
    action is consistent with a risky prediction.

    Returns: (success_count, total_count)
    """
    output_path = output_path or TRACES_PATH
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    success, total = 0, len(train_tasks)

    with open(output_path, "w", encoding="utf-8") as f:
        for i, task in enumerate(train_tasks):
            print(f"[{i+1}/{total}] {task['id']} ...", end=" ", flush=True)
            result = run_task(task, model_config, prompt_variant)

            trace_record = {
                "task_id": task["id"],
                "service": task["service"],
                "task_text": task["task"],
                "oracle": task["oracle"],
                "prompt_variant": prompt_variant,
                "trace": result["turns"],
                "result": result["result"],
                "expected_action": result["expected_action"],
                "predict_risk_result": result["predict_risk_result"],
                "final_action": result["final_action"],
                "final_tool": result["final_tool"],
                "ask_human_question": result["ask_human_question"],
                "refuse_reason": result["refuse_reason"],
                "decision_consistent": result["decision_consistent"],
                "success": (
                    result["final_action"] == "ask_human"
                    and result["decision_consistent"] is True
                ),
                "error": result.get("error"),
            }

            f.write(json.dumps(trace_record, ensure_ascii=False) + "\n")
            f.flush()

            if trace_record["success"]:
                success += 1
                print("OK")
            else:
                print(f"FAIL ({result['result']})")

    print(f"\nDone: {success}/{total} successful traces ({success/total*100:.1f}%)")
    return success, total


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Generate ask_human training traces")
    parser.add_argument("--model", default="gpt54", choices=list(MODELS.keys()),
                        help="Teacher model to use (default: gpt54)")
    parser.add_argument("--prompt", default="explicit_rules", choices=["bare", "explicit_rules"],
                        help="Prompt variant (default: explicit_rules)")
    parser.add_argument("--output", default=None, help="Output JSONL path")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of tasks")
    args = parser.parse_args()

    model_config = MODELS[args.model]
    train_tasks, _ = split_train_test(load_ask_human_tasks())

    if args.limit:
        train_tasks = train_tasks[:args.limit]

    print(f"Generating traces: model={args.model}, prompt={args.prompt}, tasks={len(train_tasks)}")
    generate_training_traces(train_tasks, model_config, args.prompt, args.output)


if __name__ == "__main__":
    main()
