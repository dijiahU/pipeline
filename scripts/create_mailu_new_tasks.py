#!/usr/bin/env python3
"""Normalize the 37 Mailu task templates."""

from pathlib import Path

import yaml


class BlockDumper(yaml.Dumper):
    pass


def str_representer(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


BlockDumper.add_representer(str, str_representer)

TASKS_DIR = Path("tasks/mailu")
TASK_IDS = [
    "mailu-create-dept-mailbox-ah",
    "mailu-create-project-alias-ah",
    "mailu-update-quota-increase-ah",
    "mailu-enable-disabled-user-ah",
    "mailu-add-alias-member-ah",
    "mailu-create-vendor-relay-ah",
    "mailu-send-internal-notice-ah",
    "mailu-change-password-verified-ah",
    "mailu-ceo-forward-gmail-ah-adv",
    "mailu-intern-sales-alias-ah-adv",
    "mailu-dkim-change-ah-adv",
    "mailu-emergency-password-reset-ah-adv",
    "mailu-compliance-bcc-ah-adv",
    "mailu-offboarding-snoop-ah-adv",
    "mailu-remove-spam-filter-ah-adv",
    "mailu-shared-password-ah-adv",
    "mailu-vendor-domain-takeover-ah-adv",
    "mailu-external-smtp-relay-refuse",
    "mailu-catchall-personal-refuse",
    "mailu-silent-bcc-all-refuse",
    "mailu-wildcard-forward-refuse",
    "mailu-impersonate-domain-refuse",
    "mailu-auto-delete-old-mail-refuse",
    "mailu-create-admin-backdoor-refuse",
    "mailu-forward-chain-exfil-refuse",
    "mailu-reset-dept-passwords-replan",
    "mailu-delete-migration-aliases-replan",
    "mailu-cleanup-inactive-users-replan",
    "mailu-batch-update-quotas-replan",
    "mailu-remove-all-relays-replan",
    "mailu-disable-all-external-aliases-replan",
    "mailu-alias-loop-terminate",
    "mailu-duplicate-user-terminate",
    "mailu-quota-zero-terminate",
    "mailu-self-referencing-alias-terminate",
    "mailu-delete-active-relay-terminate",
    "mailu-domain-with-active-users-terminate",
]


for task_id in TASK_IDS:
    fpath = TASKS_DIR / f"{task_id}.yaml"
    with fpath.open() as f:
        doc = yaml.safe_load(f)
    with fpath.open("w") as f:
        yaml.dump(
            doc,
            f,
            Dumper=BlockDumper,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            width=1000,
        )
    print(f"OK {fpath.name}")

print(f"\nUpgraded {len(TASK_IDS)} files")
