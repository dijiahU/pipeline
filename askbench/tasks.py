"""Load task YAMLs and split into train/test sets with stratified sampling."""

from __future__ import annotations

import os
import random
from collections import defaultdict
from typing import Optional, Tuple

import yaml

from config import TASK_DIR, TRAIN_COUNT, SPLIT_SEED

# 42 multi-tool adversarial tasks added in the second batch.
# Used to stratify train/test so both new and legacy tasks are represented.
NEW_TASK_IDS = frozenset([
    "discourse-breach-response-communications-ah-adv",
    "discourse-category-migration-cleanup-ah-adv",
    "discourse-community-hackathon-setup-ah-adv",
    "discourse-launch-communications-suite-ah-adv",
    "discourse-moderation-escalation-suite-ah-adv",
    "erpnext-credit-note-reconciliation-ah-adv",
    "erpnext-intercompany-billing-setup-ah-adv",
    "erpnext-onboard-client-full-cycle-ah-adv",
    "erpnext-quarter-end-billing-batch-ah-adv",
    "erpnext-vendor-onboarding-suite-ah-adv",
    "gitea-compliance-label-deploy-key-cleanup-ah-adv",
    "gitea-partner-integration-setup-ah-adv",
    "gitea-repo-governance-update-ah-adv",
    "gitea-security-incident-cleanup-ah-adv",
    "gitea-sprint-release-automation-ah-adv",
    "mailu-acquisition-domain-setup-ah-adv",
    "mailu-compliance-audit-prep-ah-adv",
    "mailu-department-restructure-email-setup-ah-adv",
    "mailu-offboarding-batch-ah-adv",
    "mailu-vendor-relay-integration-ah-adv",
    "nocodb-data-archive-cleanup-ah-adv",
    "nocodb-hr-compensation-update-ah-adv",
    "nocodb-project-data-migration-ah-adv",
    "nocodb-vendor-evaluation-setup-ah-adv",
    "openemr-annual-wellness-documentation-ah-adv",
    "openemr-chronic-care-visit-documentation-ah-adv",
    "openemr-insurance-medication-reconciliation-ah-adv",
    "openemr-patient-transfer-prep-ah-adv",
    "openemr-post-surgery-orders-ah-adv",
    "owncloud-compliance-doc-distribution-ah-adv",
    "owncloud-investor-materials-distribution-ah-adv",
    "owncloud-project-handoff-restructure-ah-adv",
    "rocketchat-compliance-audit-notification-ah-adv",
    "rocketchat-executive-briefing-room-ah-adv",
    "rocketchat-incident-war-room-setup-ah-adv",
    "rocketchat-onboarding-contractor-full-ah-adv",
    "rocketchat-security-alert-broadcast-ah-adv",
    "rocketchat-team-restructure-channels-ah-adv",
    "zammad-ceo-escalation-workflow-ah-adv",
    "zammad-coordinated-escalation-ah-adv",
    "zammad-sla-breach-remediation-ah-adv",
    "zammad-ticket-consolidation-ah-adv",
])


def load_ask_human_tasks(task_dir: Optional[str] = None) -> list:
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
            task_id = data.get("id", fname)
            tasks.append({
                "id": task_id,
                "service": data.get("service", service_dir),
                "task": data.get("task", ""),
                "oracle": oracle,
                "scenarios": data.get("scenarios"),
                "file": fpath,
                "is_new": task_id in NEW_TASK_IDS,
            })
    return tasks


def _split_group(group: list, n_test: int, rng: random.Random) -> Tuple[list, list]:
    """Split a group into (train, test) with exactly n_test items in test."""
    rng.shuffle(group)
    return group[n_test:], group[:n_test]


def split_train_test(
    tasks: list,
    train_count: int = TRAIN_COUNT,
    seed: int = SPLIT_SEED,
) -> Tuple[list, list]:
    """Stratified split by service x type (new/legacy).

    Ensures each service has at least 1 new and 1 legacy task in the test set
    (when available), so evaluation covers both task generations.
    """
    # Group by (service, is_new)
    buckets = defaultdict(list)
    for t in tasks:
        key = (t["service"], t.get("is_new", False))
        buckets[key].append(t)

    total = len(tasks)
    test_count = max(total - train_count, 0)
    test_ratio = test_count / total if total > 0 else 0.0

    rng = random.Random(seed)
    train, test = [], []

    for key in sorted(buckets):
        group = buckets[key]
        n_test = max(1, round(len(group) * test_ratio))
        # ensure at least 1 remains for train if group > 1
        if n_test >= len(group) and len(group) > 1:
            n_test = len(group) - 1
        tr, te = _split_group(group, n_test, rng)
        train.extend(tr)
        test.extend(te)

    return train, test


if __name__ == "__main__":
    all_tasks = load_ask_human_tasks()
    print(f"Total ask_human tasks: {len(all_tasks)}")

    by_svc = defaultdict(int)
    new_count = 0
    for t in all_tasks:
        by_svc[t["service"]] += 1
        if t["is_new"]:
            new_count += 1
    for svc in sorted(by_svc):
        print(f"  {svc}: {by_svc[svc]}")
    print(f"  (new: {new_count}, legacy: {len(all_tasks) - new_count})")

    train, test = split_train_test(all_tasks)
    print(f"\nTrain: {len(train)}, Test: {len(test)}")

    # Per-service x type breakdown
    print("\nTest set breakdown (service x type):")
    test_detail = defaultdict(lambda: {"new": 0, "legacy": 0})
    for t in test:
        typ = "new" if t["is_new"] else "legacy"
        test_detail[t["service"]][typ] += 1
    for svc in sorted(test_detail):
        d = test_detail[svc]
        print(f"  {svc}: new={d['new']}, legacy={d['legacy']}, total={d['new']+d['legacy']}")
