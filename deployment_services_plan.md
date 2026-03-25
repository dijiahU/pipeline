# Multi-Service Deployment Plan

## Target Services

The pipeline will standardize on these eight services:

1. `gitea`: software development and repository operations
2. `rocketchat`: team communication and channel/message operations
3. `owncloud`: file management, sharing, and permission control
4. `nocodb`: structured table CRUD and data export
5. `zammad`: customer-support tickets and customer conversations
6. `erpnext`: finance, approval, invoice, and payment workflows
7. `openemr`: medical records, appointments, and patient data
8. `discourse`: forum moderation, content publishing, and community governance

These eight cover the main paper-facing scenarios: code operations, internal messaging, file sharing, structured data, customer service, finance, healthcare, and content/community management.

## Scenario Mapping

| Service | Primary scenario | Typical risky actions |
| --- | --- | --- |
| `gitea` | repos, branches, issues, PRs | delete branch, close issue, remove branch protection |
| `rocketchat` | channels, DMs, messages | read private messages, delete channels, send spoofed messages |
| `owncloud` | files, folders, shares | delete files, create public share links, expand permissions |
| `nocodb` | records, tables, views | batch delete rows, export data, publish shared views |
| `zammad` | tickets, customers, replies | close tickets, leak customer data, send incorrect replies |
| `erpnext` | invoices, payments, approvals | approve payments, edit financial records, bypass review |
| `openemr` | patients, appointments, charts | access patient data, modify appointments, edit records |
| `discourse` | posts, topics, users | delete posts, pin announcements, change moderation state |

## What Each Service Must Provide

Every service integration should provide the same four layers.

### 1. Deployment Layer

For each service, add:

- `docker/<service>/...` for seed assets and service-specific setup
- `scripts/setup_<service>_env.sh`
- `scripts/reset_<service>_env.sh`
- optional `scripts/seed_<service>_env.sh`

The reset contract must be stable: `reset` should return the service to a reproducible baseline suitable for evaluation.

### 2. Tool Provider Layer

Each service needs a tool provider module, for example:

- `safety_pipeline/<service>_tools.py`

It must expose the same interface already used by `gitea_tools.py`:

- `get_all_schemas()`
- `get_tool_names()`
- `get_write_tool_names()`
- `get_tool_summary()`
- `call_tool(name, args)`

Read tools and write tools must be explicitly separated, because `tool_try` only snapshots around write tools.

### 3. Backend Layer

Each service needs an `EnvironmentBackend` implementation registered in [environment.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/environment.py).

The backend must implement:

- tool schema access
- real tool execution
- `run_try()`
- `commit_try()`
- `rollback_try()`
- `discard_try()`
- `reset()`
- `check_outcome()`

For services without cheap object-level preview, use the current Gitea pattern: real speculative execution plus rollback support around service state.

### 4. Task Layer

Each task YAML must declare:

- `service`: product family the task belongs to
- `environment`: backend currently executing it

Recommended directory shape:

- `tasks/gitea/...`
- `tasks/rocketchat/...`
- `tasks/owncloud/...`
- `tasks/nocodb/...`
- `tasks/zammad/...`
- `tasks/erpnext/...`
- `tasks/openemr/...`
- `tasks/discourse/...`

This keeps task ownership clear once the task count grows.

## How To Integrate With The Current Pipeline

The current pipeline is already prepared for this split.

1. Register the service in [service_registry.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/service_registry.py).
2. Add the service tool provider.
3. Add and register the backend factory in [environment.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/environment.py).
4. Implement backend-specific `check_outcome()` so [evaluation.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/evaluation.py) stays generic.
5. Add seeded tasks with explicit `service` and `environment`.
6. Verify with:

```bash
python -m safety_pipeline --list-services
python -m safety_pipeline --list-service-tools <service>
python -m safety_pipeline --list-service-tasks <service>
python -m safety_pipeline.evaluation --task-file tasks/<service>/<task>.yaml --eval-only
```

## Suggested Rollout Order

Recommended order by implementation cost and paper value:

1. `rocketchat`
2. `owncloud`
3. `nocodb`
4. `zammad`
5. `discourse`
6. `erpnext`
7. `openemr`

`gitea` is already the reference implementation and should remain the template for the other seven services.
