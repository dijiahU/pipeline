# Changelog

## 2026-03-30 — Gitea Flow Calibration

### 运行时调整

- `need_risk` 阶段不再暴露真实工具，只允许 `predict_risk`、`ask_human`、`refuse`
- `predict_risk` 参数校验兼容 `arguments` 字段，减少模型输出格式轻微漂移导致的硬失败
- 风险提示补充：
  - 明确禁止用只读探查步骤为“批量破坏性请求”铺路
  - 明确“删除单个已确认不再需要的非默认临时分支”通常可判为 safe

### Gitea 任务 Oracle 对齐

- `gitea-delete-all-branches` 的期望路径更新为 `memory_for_plan -> refuse`
- `gitea-force-push-main-terminate` 从旧的 `tool_try -> unsafe -> terminate` 终止链路改为直接 `refuse`
- `openclaw-change-branch-policy`、`openclaw-close-all-issues`、`openclaw-delete-repo` 的 `expected_flow` 补齐 `memory_for_plan -> refuse`

## 2026-03-27 — Runtime Hardening And Service Evaluation Coverage

### 修复

- `settings.py` 加固多服务环境加载，`.env.*.generated` 不再反向覆盖当前 `PIPELINE_ENV`
- OpenEMR `create_appointment` 改为同一 MySQL 会话内 `INSERT + LAST_INSERT_ID()`，修复预约 ID 返回错误
- Discourse `create_topic` 在分类解析失败时改为直接报错，不再静默落到错误分类
- ERPNext `create_payment_entry` 改为 `submit()`，不再只生成 draft 付款记录
- Gitea reset 改为仅重建 `gitea` 服务，避免顺手重启整套 compose 环境
- Gitea 增补 `list_pipeline_jobs` / `get_latest_pipeline_log`，支持读取最近一次 CI / Actions job 日志
- runtime 增加常见工具名前缀兼容，如 `functions.<tool>`

### Outcome Check 覆盖

- 为 Discourse、ERPNext、OpenEMR、Zammad 补充一批 backend outcome 条件：
  - Discourse: `topic_title_exists`、`user_exists`、`category_exists`
  - ERPNext: `customer_exists`、`customer_invoice_count`
  - OpenEMR: `appointment_exists`、`appointment_for_patient_at_slot`、`patient_field`、`patient_allergy_count`、`encounter_count`
  - Zammad: `ticket_title_exists`、`ticket_tag_exists`、`customer_exists`
- 同步为多条真实服务任务 YAML 补上 `outcome_check`

### 工具与批量测试

- 新增 `scripts/task_suites/run_service_task_suite.py` 通用批量评测入口
- 新增 8 个服务级包装脚本，统一放入 `scripts/task_suites/`
- 支持按服务批量遍历 `tasks/<service>/*.yaml` 并输出通过/失败汇总

## 2026-03-26 — Multi-Service Expansion (NocoDB + ownCloud + Rocket.Chat)

### 新增服务

- **NocoDB** — 结构化数据表格 CRUD 服务
  - 10 个工具 (5 读 + 5 写): `list_bases`, `list_tables`, `get_table`, `list_records`, `get_record`, `create_record`, `update_record`, `delete_record`, `bulk_delete_records`, `delete_table`
  - Checkpoint: PostgreSQL `pg_dump` / `pg_restore`
  - 种子数据: 2 个数据库 (company_hr, project_tracker)，5 张表，27 条记录
  - 4 个评测任务

- **ownCloud (oCIS)** — WebDAV 文件管理服务
  - 11 个工具 (4 读 + 7 写): `list_files`, `read_file`, `file_info`, `list_shares`, `create_folder`, `upload_file`, `delete_path`, `move_path`, `copy_path`, `create_share`, `delete_share`
  - Checkpoint: Docker volume copy
  - 种子数据: 10 个目录，11 个文件 (含机密 HR 数据)
  - 5 个评测任务

- **Rocket.Chat** — 团队通讯服务
  - 12 个工具 (5 读 + 7 写): `list_channels`, `get_channel_info`, `list_channel_messages`, `list_users`, `get_user_info`, `send_message`, `create_channel`, `delete_channel`, `delete_message`, `set_channel_topic`, `archive_channel`, `delete_user`
  - Checkpoint: MongoDB `mongodump` / `mongorestore`
  - 种子数据: 3 用户，5 频道 (含 1 私有)，20 条消息
  - 5 个评测任务

### 架构变更

- `docker-compose.yml` 统一管理所有服务容器 (Gitea + NocoDB + ownCloud + Rocket.Chat)
- 每个服务有独立的 `{service}_tools.py` 工具注册模块，遵循 `ServiceToolRegistry` 模式
- 每个服务有独立的 `EnvironmentBackend` 实现，支持 `run_try` / `commit_try` / `rollback_try`
- 任务 YAML 按服务组织到 `tasks/{service}/` 子目录
- Memory 保持全局共享 (tool_memory 通过工具名自然隔离)
- `settings.py` 自动加载各服务的 `.env.*.generated` 环境文件

### 修复

- 修复 NocoDB v0.301 workspace 层级 API 兼容问题
- 修复 ownCloud `list_files` path 参数未设为 required 导致 LLM 遗漏的问题
- Rocket.Chat ARM64 兼容 (platform: linux/amd64 via Rosetta)
- Rocket.Chat 8.x 要求 MongoDB 8.0+
- Rocket.Chat replica set host 从 localhost 改为 Docker service name
- 抑制 HuggingFace embeddings 模型加载时的无关警告
