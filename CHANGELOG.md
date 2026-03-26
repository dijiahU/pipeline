# Changelog

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
