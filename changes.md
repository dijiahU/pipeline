# Changes

## 0. 多服务抽象重构

- 新增 `safety_pipeline/service_registry.py`，把 8 个目标部署服务 (`gitea`、`rocketchat`、`owncloud`、`nocodb`、`zammad`、`erpnext`、`openemr`、`discourse`) 统一注册。
- 新增 `safety_pipeline/service_tools.py`，抽出通用服务工具注册协议；`gitea_tools.py` 已切到该协议，并显式标注写工具。
- 新增 `safety_pipeline/task_catalog.py`，递归扫描 `tasks/` 下的任务 YAML，并按 `service` 字段建立任务索引。
- `safety_pipeline/environment.py` 改成后端工厂注册，`outcome_check` 下沉到后端实现，后续新增服务无需继续在 `evaluation.py` 里堆服务专属 API 逻辑。
- `safety_pipeline/runtime.py` 新增 `--list-services`、`--list-service-tasks <service>`、`--list-service-tools <service>`，可以直接查看服务、任务和工具注册状态。
- 当前 `openclaw` 示例任务已补 `service: gitea`，把任务归属服务和执行后端 `environment` 解耦。
- 新增 `tasks/README.md`，说明目标服务、兼容服务和当前任务归属。

本轮代码基线仍以 Gitea 兼容运行时为主，但任务集已切换为 `openclaw` 场景，并开始为多服务扩展做结构调整。

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

## 5. 后续清理

- 删除了失效的 `docker/gitea/scripts/build_gitea_image.sh` 旧镜像烘焙路径。
- 移除了不再生效的 `SANDBOX_MODE` 环境配置。
- 将 `criterion.md`、`branches.md`、`TODO.md` 同步到当前的 `tool_try -> try_commit` 与 `unsafe -> ask_human|terminate` 语义。
- 为 `openclaw-clean-branches` 增加 NPC 场景，并让自动评测在非交互模式下明确处理 `ask_human`。

## 6. 失败轨迹保留

- `safety_pipeline/runtime.py` 现在会为 `aborted`、`max_turns_exceeded`、`max_tool_rounds_exceeded` 落一条失败 case 到 `experience_memory.json`。
- 失败 case 会保留 `status`、错误原因和当时的 `flow_tool_calls`，便于回看评测失败轨迹。
- 失败 session 默认不导出到 `sft_dataset.jsonl`，避免把非法调用或半截流程混进训练样本。

## 7. 已完成验证

- `python -m py_compile safety_pipeline/*.py`
- `bash scripts/reset_env.sh`
- `python -m safety_pipeline.evaluation --task-file tasks/openclaw-change-branch-policy.yaml --eval-only`
- `python -m safety_pipeline.evaluation --task-file tasks/openclaw-delete-repo.yaml --eval-only`

## 8. 新增真实服务后端

- 新增 `zammad`、`discourse`、`erpnext`、`openemr` 的真实工具提供层与真实 backend：
  - `safety_pipeline/zammad_tools.py`
  - `safety_pipeline/discourse_tools.py`
  - `safety_pipeline/erpnext_tools.py`
  - `safety_pipeline/openemr_tools.py`
  - `safety_pipeline/zammad_backend.py`
  - `safety_pipeline/discourse_backend.py`
  - `safety_pipeline/erpnext_backend.py`
  - `safety_pipeline/openemr_backend.py`
- `safety_pipeline/environment.py` 现在为上述服务注册真实 backend，不再让 `openemr` 走 fixture-only 执行路径。
- `safety_pipeline/service_registry.py` 已同步所有 8 个目标服务的真实部署状态说明。

## 9. 新增部署与回滚资产

- 新增 `scripts/setup_zammad_env.sh`、`scripts/reset_zammad_env.sh`、`scripts/zammad_env_common.sh`。
- 新增 `scripts/setup_discourse_env.sh`、`scripts/reset_discourse_env.sh`、`scripts/discourse_env_common.sh`。
- 新增 `scripts/setup_erpnext_env.sh`、`scripts/reset_erpnext_env.sh`、`scripts/erpnext_env_common.sh`。
- 新增 `scripts/setup_openemr_env.sh`、`scripts/reset_openemr_env.sh`、`scripts/openemr_env_common.sh`。
- 新增 `docker/discourse/...`、`docker/erpnext/...`、`docker/openemr/...`、`docker/zammad/...` 服务专属 seed 与 compose 资产。
- `openemr` 采用真实 OpenEMR + MariaDB，两阶段 bootstrap 安装后再导入持久化 `sites + SQL`，并通过 `mysqldump` + `sites copy` 实现 `reset` 与 `tool_try` 回滚。

## 10. OpenEMR 真实部署落地

- `openemr` 从本地 JSON fixture 升级到真实 Docker 部署，入口为 `http://localhost:8083`。
- 新增真实患者与预约基线：
  - `PT-100` / `John Chen`
  - `PT-101` / `Maria Gomez`
  - `APT-100` / `2026-03-28 09:00`
  - `APT-101` / `2026-03-28 14:30`
- 真实工具已覆盖：
  - `list_patients`
  - `get_patient`
  - `list_appointments`
  - `add_patient_note`
  - `reschedule_appointment`
  - `delete_patient`

## 11. 文档同步

- `README.md` 已同步 8 个服务的当前真实部署状态、启动命令、默认入口和端口。
- `requirements.txt` 本轮无需新增依赖；现有 `requests` 即可覆盖新增服务工具的 HTTP/辅助调用需求。
- `.gitignore` 已补充 `discourse` / `erpnext` / `openemr` 的运行期 shared、baseline 和官方仓库目录，避免把本地生成状态提交到仓库。
- `scripts/setup_discourse_env.sh` 现在会在本地缺少 `discourse_docker` 时自动拉取官方 launcher 仓库并固定到当前验证版本。

## 12. 新增验证

- `bash scripts/reset_zammad_env.sh`
- `bash scripts/reset_discourse_env.sh`
- `bash scripts/reset_erpnext_env.sh`
- `bash scripts/setup_openemr_env.sh`
- `bash scripts/reset_openemr_env.sh`
- `python -m safety_pipeline --list-service-tools zammad`
- `python -m safety_pipeline --list-service-tools discourse`
- `python -m safety_pipeline --list-service-tools erpnext`
- `python -m safety_pipeline --list-service-tools openemr`
- `python -m safety_pipeline --list-service-tasks zammad`
- `python -m safety_pipeline --list-service-tasks discourse`
- `python -m safety_pipeline --list-service-tasks erpnext`
- `python -m safety_pipeline --list-service-tasks openemr`
- `python -m safety_pipeline.evaluation --task-file tasks/openemr/openemr-list-appointments.yaml --eval-only`
- `python -m safety_pipeline.evaluation --task-file tasks/openemr/openemr-read-patient.yaml --eval-only`
- `python -m safety_pipeline.evaluation --task-file tasks/openemr/openemr-reschedule-appointment.yaml --eval-only`
- `python -m safety_pipeline.evaluation --task-file tasks/openemr/openemr-delete-patient.yaml --eval-only`
