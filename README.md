# Agent Safety Pipeline

决策驱动、step-level 的安全执行 pipeline：每轮只推进一个最小可执行 step，再根据证据决定进入真实执行、试执行提交、重规划、向人追问或拒绝。

## 服务部署状态

| 服务 | 领域 | 状态 | 读工具 | 写工具 | Checkpoint 方式 |
|------|------|------|--------|--------|----------------|
| Gitea | 代码托管 | **已实现** | 8 | 3 | Docker volume/bind copy |
| NocoDB | 数据库表格 | **已实现** | 5 | 5 | pg_dump / pg_restore |
| ownCloud | 文件管理 | **已实现** | 4 | 7 | Docker volume copy |
| Rocket.Chat | 团队通讯 | **已实现** | 5 | 7 | mongodump / mongorestore |
| Zammad | 客户支持 | **已实现** | 3 | 3 | pg_dump / pg_restore |
| ERPNext | 财务/ERP | **已实现** | 3 | 3 | sites copy + mysqldump |
| OpenEMR | 医疗健康 | **已实现** | 3 | 3 | sites copy + mysqldump |
| Discourse | 社区论坛 | **已实现** | 3 | 3 | shared dir copy |

## 运行方式

安装依赖：

```bash
pip install -r requirements.txt
```

当前不需要为 `openemr` 额外增加 Python 依赖，现有 `requirements.txt` 已覆盖运行所需库。

配置环境变量（项目根目录 `.env`）：

```env
OPENAI_API_KEY=your_openai_api_key
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_MODEL=openai/gpt-4o
PIPELINE_ENV=gitea
```

### 环境启动与重置

```bash
# Gitea
bash scripts/setup_env.sh
bash scripts/reset_env.sh

# NocoDB
bash scripts/setup_nocodb_env.sh
bash scripts/reset_nocodb_env.sh

# ownCloud
bash scripts/setup_owncloud_env.sh
bash scripts/reset_owncloud_env.sh

# Rocket.Chat
bash scripts/setup_rocketchat_env.sh
bash scripts/reset_rocketchat_env.sh

# Zammad
bash scripts/setup_zammad_env.sh
bash scripts/reset_zammad_env.sh

# Discourse
bash scripts/setup_discourse_env.sh
bash scripts/reset_discourse_env.sh

# ERPNext
bash scripts/setup_erpnext_env.sh
bash scripts/reset_erpnext_env.sh

# OpenEMR
bash scripts/setup_openemr_env.sh
bash scripts/reset_openemr_env.sh
```

### 默认入口与账号

| 服务 | 地址 | 默认账号 |
|------|------|----------|
| Zammad | `http://localhost:8081` | `admin@example.com / Admin123!` |
| Discourse | `http://localhost:4200` | `admin@example.com / Admin123!Admin!` |
| ERPNext | `http://localhost:8082` | `Administrator / admin` |
| OpenEMR | `http://localhost:8083` | `admin / Admin123!` |

说明：
- `scripts/setup_discourse_env.sh` 会在缺少 launcher 时自动拉取官方 `discourse_docker` 到 `docker/discourse/discourse_docker/`。
- `docker/discourse/shared/`、`docker/erpnext/shared/`、`docker/erpnext/baseline/`、`docker/openemr/shared/`、`docker/openemr/baseline/` 都是本地运行期产物，不纳入版本控制。

### 运行任务

```bash
python -m safety_pipeline --list-services
python -m safety_pipeline --list-service-tasks gitea
python -m safety_pipeline --list-service-tools gitea

# 指定服务运行任务
PIPELINE_ENV=gitea python -m safety_pipeline --task-file tasks/gitea/openclaw-read-readme.yaml
PIPELINE_ENV=nocodb python -m safety_pipeline --task-file tasks/nocodb/nocodb-list-employees.yaml
PIPELINE_ENV=owncloud python -m safety_pipeline --task-file tasks/owncloud/owncloud-list-documents.yaml
PIPELINE_ENV=rocketchat python -m safety_pipeline --task-file tasks/rocketchat/rocketchat-list-channels.yaml
PIPELINE_ENV=zammad python -m safety_pipeline --task-file tasks/zammad/zammad-list-open-tickets.yaml
PIPELINE_ENV=discourse python -m safety_pipeline --task-file tasks/discourse/discourse-list-announcements.yaml
PIPELINE_ENV=erpnext python -m safety_pipeline --task-file tasks/erpnext/erpnext-list-unpaid-invoices.yaml
PIPELINE_ENV=openemr python -m safety_pipeline --task-file tasks/openemr/openemr-read-patient.yaml

# 仅评测（不执行）
python -m safety_pipeline.evaluation --task-file tasks/gitea/openclaw-close-all-issues.yaml --eval-only
python -m safety_pipeline.evaluation --task-file tasks/openemr/openemr-reschedule-appointment.yaml --eval-only
```

### Gitea 常用测试命令

```bash
# 重置本地 Gitea 种子环境
bash scripts/reset_env.sh

# 查看当前 Gitea 工具和任务
python -m safety_pipeline --list-service-tools gitea
python -m safety_pipeline --list-service-tasks gitea

# 直接跑一条 Gitea 任务
PIPELINE_ENV=gitea python -m safety_pipeline --task-file tasks/gitea/openclaw-read-readme.yaml

# 跑一条 Gitea 评测任务
python -m safety_pipeline.evaluation --task-file tasks/gitea/gitea-read-issue-detail.yaml

# 跑整套 Gitea 任务评测
bash scripts/task_suites/test_gitea_tasks.sh

# 遇到第一条失败就停止
bash scripts/task_suites/test_gitea_tasks.sh --stop-on-fail

# 只校验 outcome，不重新执行 pipeline
python -m safety_pipeline.evaluation --task-file tasks/gitea/openclaw-delete-repo.yaml --eval-only
```

## 主要文件

- `safety_pipeline/runtime.py` — 主流程编排与状态机
- `safety_pipeline/environment.py` — `EnvironmentBackend` 抽象与各服务后端实现
- `safety_pipeline/gitea_tools.py` — Gitea API 工具注册
- `safety_pipeline/nocodb_tools.py` — NocoDB API 工具注册
- `safety_pipeline/owncloud_tools.py` — ownCloud WebDAV/OCS 工具注册
- `safety_pipeline/rocketchat_tools.py` — Rocket.Chat REST API 工具注册
- `safety_pipeline/zammad_tools.py` — Zammad REST API 工具注册
- `safety_pipeline/discourse_tools.py` — Discourse REST API 工具注册
- `safety_pipeline/erpnext_tools.py` — ERPNext/Frappe 工具注册
- `safety_pipeline/openemr_tools.py` — OpenEMR MariaDB 工具注册
- `safety_pipeline/service_registry.py` — 目标服务注册表
- `safety_pipeline/evaluation.py` — 任务级评测框架
- `docker-compose.yml` — Gitea / NocoDB / ownCloud / Rocket.Chat / Zammad 容器编排
- `docker/{service}/docker-compose.yml` — ERPNext / OpenEMR 等服务独立编排
- `tasks/{service}/*.yaml` — 按服务分类的评测任务

## Docker 服务端口

| 服务 | 端口 | 数据库 |
|------|------|--------|
| Gitea | 3000 | SQLite |
| NocoDB | 8080 | PostgreSQL (5432) |
| ownCloud oCIS | 9200 (HTTPS) | 内置 |
| Rocket.Chat | 3100 | MongoDB (27017) |
| Zammad | 8081 | PostgreSQL |
| ERPNext | 8082 | MariaDB / Redis |
| OpenEMR | 8083 | MariaDB |
| Discourse | 4200 | 容器内 PostgreSQL / Redis |

## 说明

- `memory_for_plan` 和 `memory_for_tool` 由代码自动执行
- 若缺少 `faiss-cpu`，plan memory 自动降级为空召回
- `tool_try` 使用真实试执行；`unsafe -> ask_human` 前自动回滚
- 任务 YAML 要求声明 `service` 与 `environment` 字段
- Memory 全局共享，tool_memory 通过工具名自然隔离
