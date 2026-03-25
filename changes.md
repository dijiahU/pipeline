# Changes

本轮改动已将仓库从 GitLab 测试环境迁移为 Gitea-only，并把任务集切换为 `openclaw` 场景。

## 1. Gitea 环境落地

- 根目录 [docker-compose.yml](/Users/rick/Desktop/pipline/pipeline/docker-compose.yml) 改为启动本地 Gitea。
- 新增 `scripts/setup_gitea_env.sh`、`scripts/reset_gitea_env.sh`、`scripts/seed_gitea_env.sh`、`scripts/gitea_env_common.sh`。
- `scripts/setup_env.sh` 与 `scripts/reset_env.sh` 现在作为 Gitea 入口包装脚本。
- 新增 `docker/gitea/scripts/seed_gitea_data.py` 与 `docker/gitea/seed_manifest.json`，用于 reset 后自动 seed。

## 2. Runtime 与后端迁移

- 删除 `safety_pipeline/gitlab_tools.py`，新增 `safety_pipeline/gitea_tools.py`。
- `safety_pipeline/environment.py` 仅保留 `GiteaBackend`。
- `safety_pipeline/settings.py` 默认环境改为 `gitea`，并自动加载 `.env.gitea.generated`。
- `safety_pipeline/runtime.py` 的 `--env` 仅支持 `gitea`，相关提示词、工具分支与导出逻辑已同步。
- `safety_pipeline/evaluation.py` 改为直接通过 Gitea API 做 outcome 校验，并新增单仓库检查：
  - `repo_issues_open`
  - `repo_exists`
  - `branch_protected`

## 3. openclaw 测试数据与任务

- `openclaw` 从 GitHub 快照导入到本地 Gitea。
- seed 现包含：
  - `openclaw/main`
  - `openclaw/stale-docs`
  - `openclaw/renderer-upgrade`
  - 2 个 issue
  - 1 个 PR
  - `main` 分支保护
- 旧的 `sotopia` 任务已移除，替换为：
  - `tasks/openclaw-read-readme.yaml`
  - `tasks/openclaw-delete-stale-branch.yaml`
  - `tasks/openclaw-clean-branches.yaml`
  - `tasks/openclaw-close-all-issues.yaml`
  - `tasks/openclaw-change-branch-policy.yaml`
  - `tasks/openclaw-delete-repo.yaml`

## 4. 文档同步

- 更新 `README.md`、`AGENTS.md`、`CLAUDE.md`、`criterion.md`、`TODO.md` 为 Gitea/openclaw 版本。
- 删除旧的 GitLab 迁移与历史说明文档。

## 5. 已完成验证

- `python -m py_compile safety_pipeline/*.py`
- `bash scripts/reset_env.sh`
- `python -m safety_pipeline.evaluation --task-file tasks/openclaw-change-branch-policy.yaml --eval-only`
- `python -m safety_pipeline.evaluation --task-file tasks/openclaw-delete-repo.yaml --eval-only`
