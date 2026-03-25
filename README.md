# Agent Safety Pipeline

当前仓库已经切换为 **Gitea-only** 环境。主实现集中在 [`safety_pipeline/`](./safety_pipeline) 包中，采用 **decision-driven、step-level** 的安全执行 pipeline：每轮只推进一个最小可执行 step，再根据证据决定进入真实执行、预览试执行、重规划、向人追问或拒绝。

## 当前环境

- 服务后端：Gitea
- 主要真实工具：项目列表、分支、issue、PR、仓库文件、分支保护
- 写操作：`delete_branch`、`close_issue`、`update_branch_protection`
- `tool_try` 当前使用 preview 模式

## 运行方式

安装依赖：

```bash
pip install -r requirements.txt
```

配置环境变量（项目根目录 `.env`）：

```env
OPENAI_API_KEY=your_openai_api_key
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_MODEL=openai/gpt-4o
GITEA_BASE_URL=http://localhost:3000
GITEA_OWNER=root
PIPELINE_ENV=gitea
```

启动并初始化 Gitea：

```bash
bash scripts/setup_env.sh
```

重置 Gitea 环境并自动重新 seed：

```bash
bash scripts/reset_env.sh
```

运行任务：

```bash
python -m safety_pipeline --task-file tasks/openclaw-read-readme.yaml
python -m safety_pipeline --task-file tasks/openclaw-delete-stale-branch.yaml
python -m safety_pipeline.evaluation --task-file tasks/openclaw-change-branch-policy.yaml --eval-only
```

## 主要文件

- `safety_pipeline/runtime.py`：主流程编排与状态机
- `safety_pipeline/gitea_tools.py`：Gitea API 工具注册与字段映射
- `safety_pipeline/environment.py`：`EnvironmentBackend` 抽象与 `GiteaBackend`
- `safety_pipeline/evaluation.py`：任务级评测框架
- `docker-compose.yml`：Gitea 测试环境
- `scripts/setup_env.sh` / `scripts/reset_env.sh`：环境启动与重置入口
- `docker/gitea/scripts/seed_gitea_data.py`：种子数据脚本

## 说明

- `memory_for_plan` 和 `memory_for_tool` 由代码自动执行。
- 若本机缺少 `faiss-cpu`，plan memory 会自动降级为空召回，不再阻塞 pipeline。
- `tasks/*.yaml` 现在默认都以 `openclaw` 作为主要测试仓库。
