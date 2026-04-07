"""Run all 6 experiments (3 models x 2 prompts) and produce comparison report."""

import json
import os

from config import MODELS, PROMPT_VARIANTS, RESULTS_DIR
from evaluate import evaluate_results, format_metrics_table
from runner import run_task_batch
from tasks import load_ask_human_tasks, split_train_test


def run_single_experiment(
    experiment_name: str,
    tasks: list[dict],
    model_config: dict,
    prompt_variant: str,
) -> tuple[list[dict], dict]:
    """Run one experiment and return (raw_results, metrics)."""
    print(f"\n{'='*60}")
    print(f"Experiment: {experiment_name}")
    print(f"  Model: {model_config.get('model')}")
    print(f"  Prompt: {prompt_variant}")
    print(f"  Tasks: {len(tasks)}")
    print(f"{'='*60}")

    results = run_task_batch(tasks, model_config, prompt_variant)
    metrics = evaluate_results(results)

    print(f"\n  Accuracy: {metrics['accuracy']:.1%}")
    print(f"  Risk Detection: {metrics['risk_detection_rate']:.1%}")
    print(f"  Error Rate: {metrics['error_rate']:.1%}")

    return results, metrics


def run_benchmark(
    test_tasks: list[dict] | None = None,
    models: dict | None = None,
    prompt_variants: list[str] | None = None,
):
    """Run all experiments and save results.

    Default: 6 experiments = 3 models x 2 prompt variants.
    """
    if test_tasks is None:
        _, test_tasks = split_train_test(load_ask_human_tasks())
    models = models or MODELS
    prompt_variants = prompt_variants or PROMPT_VARIANTS

    os.makedirs(RESULTS_DIR, exist_ok=True)

    all_results = {}
    all_metrics = {}

    for model_name, model_config in models.items():
        for prompt_variant in prompt_variants:
            exp_name = f"{model_name}_{prompt_variant}"
            results, metrics = run_single_experiment(
                exp_name, test_tasks, model_config, prompt_variant
            )
            all_results[exp_name] = results
            all_metrics[exp_name] = metrics

            # Save per-experiment results
            exp_path = os.path.join(RESULTS_DIR, f"{exp_name}.jsonl")
            with open(exp_path, "w", encoding="utf-8") as f:
                for r in results:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # Save summary report
    report = format_metrics_table(all_metrics)
    report_path = os.path.join(RESULTS_DIR, "benchmark_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# AskBench Results\n\n")
        f.write(f"Test tasks: {len(test_tasks)}\n\n")
        f.write(report)

    print(f"\n\n{'='*60}")
    print("FINAL REPORT")
    print(f"{'='*60}\n")
    print(report)
    print(f"\nResults saved to: {RESULTS_DIR}/")
    print(f"Report: {report_path}")

    # Save metrics JSON
    metrics_path = os.path.join(RESULTS_DIR, "benchmark_metrics.json")
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(all_metrics, f, ensure_ascii=False, indent=2)

    return all_metrics


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run AskBench experiments")
    parser.add_argument(
        "--models", nargs="+", default=None,
        help=f"Models to test (default: all). Choices: {list(MODELS.keys())}",
    )
    parser.add_argument(
        "--prompts", nargs="+", default=None,
        help=f"Prompt variants (default: all). Choices: {PROMPT_VARIANTS}",
    )
    parser.add_argument(
        "--use-train", action="store_true",
        help="Use train set instead of test set (for debugging)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit number of test tasks")
    args = parser.parse_args()

    all_tasks = load_ask_human_tasks()
    train_tasks, test_tasks = split_train_test(all_tasks)

    tasks = train_tasks if args.use_train else test_tasks
    if args.limit:
        tasks = tasks[:args.limit]

    models = {k: MODELS[k] for k in args.models} if args.models else MODELS
    prompts = args.prompts or PROMPT_VARIANTS

    run_benchmark(tasks, models, prompts)


if __name__ == "__main__":
    main()
