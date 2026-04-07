"""Load task YAMLs and split into train/test sets with stratified sampling."""

import os
import random
from collections import defaultdict

import yaml

from config import TASK_DIR, TRAIN_COUNT, SPLIT_SEED


def load_ask_human_tasks(task_dir: str | None = None) -> list[dict]:
    """Load all tasks where oracle.preferred_action == 'ask_human'."""
    task_dir = task_dir or TASK_DIR
    tasks = []
    for service_dir in sorted(os.listdir(task_dir)):
        service_path = os.path.join(task_dir, service_dir)
        if not os.path.isdir(service_path):
            continue
        for fname in sorted(os.listdir(service_path)):
            if not fname.endswith((".yaml", ".yml")):
                continue
            fpath = os.path.join(service_path, fname)
            with open(fpath, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not data:
                continue
            oracle = data.get("oracle") or {}
            if oracle.get("preferred_action") != "ask_human":
                continue
            tasks.append({
                "id": data.get("id", fname),
                "service": data.get("service", service_dir),
                "task": data.get("task", ""),
                "oracle": oracle,
                "scenarios": data.get("scenarios"),
                "file": fpath,
            })
    return tasks


def split_train_test(
    tasks: list[dict],
    train_count: int = TRAIN_COUNT,
    seed: int = SPLIT_SEED,
) -> tuple[list[dict], list[dict]]:
    """Stratified split by service, ensuring each service appears in both sets."""
    by_service: dict[str, list[dict]] = defaultdict(list)
    for t in tasks:
        by_service[t["service"]].append(t)

    total = len(tasks)
    train_ratio = train_count / total if total > 0 else 0.0

    rng = random.Random(seed)
    train, test = [], []

    for service in sorted(by_service):
        group = by_service[service]
        rng.shuffle(group)
        n_train = max(1, round(len(group) * train_ratio))
        # ensure at least 1 in test if group size > 1
        if n_train >= len(group) and len(group) > 1:
            n_train = len(group) - 1
        train.extend(group[:n_train])
        test.extend(group[n_train:])

    return train, test


if __name__ == "__main__":
    all_tasks = load_ask_human_tasks()
    print(f"Total ask_human tasks: {len(all_tasks)}")

    by_svc = defaultdict(int)
    for t in all_tasks:
        by_svc[t["service"]] += 1
    for svc in sorted(by_svc):
        print(f"  {svc}: {by_svc[svc]}")

    train, test = split_train_test(all_tasks)
    print(f"\nTrain: {len(train)}, Test: {len(test)}")

    train_svc = defaultdict(int)
    test_svc = defaultdict(int)
    for t in train:
        train_svc[t["service"]] += 1
    for t in test:
        test_svc[t["service"]] += 1
    print("\nPer-service split:")
    for svc in sorted(set(list(train_svc) + list(test_svc))):
        print(f"  {svc}: train={train_svc[svc]}, test={test_svc[svc]}")
