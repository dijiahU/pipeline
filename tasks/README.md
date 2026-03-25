# Task Service Index

This repository now targets these eight deployment services:

- `gitea`, `rocketchat`, `owncloud`, `nocodb`, `zammad`, `erpnext`, `openemr`, `discourse`

Each task YAML should declare both:

- `service`: which product family the task belongs to
- `environment`: which backend implementation currently executes it

Current task ownership:

- `gitea`
  - `openclaw-read-readme`
  - `openclaw-delete-stale-branch`
  - `openclaw-clean-branches`
  - `openclaw-close-all-issues`
  - `openclaw-change-branch-policy`
  - `openclaw-delete-repo`

You can inspect the live service/task index with:

```bash
python -m safety_pipeline --list-services
python -m safety_pipeline --list-service-tasks gitea
python -m safety_pipeline --list-service-tools gitea
python -m safety_pipeline --list-service-tasks rocketchat
```
