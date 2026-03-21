# Changes — GitLab 后端接入 + 评测框架 + Package 化清理

本文档记录当前代码状态对应的主要修改内容。

## 新增文件

| 文件 | 说明 |
|------|------|
| `docker-compose.yml` | 单服务 compose 文件，拉取预构建 GitLab 镜像 `ghcr.io/theagentcompany/servers-gitlab:1.0.0` |
| `scripts/setup_env.sh` | 容器启动脚本，轮询 GitLab API 直到健康就绪 |
| `tasks/safety-list-projects.yaml` | 评测任务：列出所有项目（safe path） |
| `tasks/safety-delete-branch.yaml` | 评测任务：删除单个分支（try path） |
| `tasks/safety-delete-repos.yaml` | 评测任务：删除所有仓库（refuse path） |
| `tasks/safety-close-all-issues.yaml` | 评测任务：关闭所有 issue（refuse path） |
| `tasks/safety-clean-branches.yaml` | 评测任务：清理旧分支（ask_human path） |
| `tasks/safety-change-branch-policy.yaml` | 评测任务：修改分支保护策略（refuse path） |
| `safety_pipeline/__main__.py` | package CLI 入口，支持 `python -m safety_pipeline` |
| `safety_pipeline/settings.py` | 运行常量、路径和环境变量封装 |
| `safety_pipeline/llm.py` | OpenAI 客户端和模型调用封装 |
| `safety_pipeline/console.py` | 控制台输出辅助 |
| `safety_pipeline/state.py` | 会话状态、摘要、风险记录、对话更新辅助 |
| `safety_pipeline/memory.py` | experience memory、tool memory、FAISS 检索与 memory 相关辅助 |
| `safety_pipeline/exceptions.py` | 自定义执行异常 `ToolExecutionError` |
| `safety_pipeline/runtime.py` | 主流程编排入口，包含 flow-control、执行分发、导出逻辑 |
| `safety_pipeline/environment.py` | 环境后端抽象层，定义 `EnvironmentBackend` 和 `GitLabBackend` |
| `safety_pipeline/gitlab_tools.py` | GitLab API 工具模块，采用 `@gitlab_tool()` 和 `_REGISTRY` 模式 |
| `safety_pipeline/evaluation.py` | 任务级评测框架，支持 decision / outcome / behavior 检查 |

## 删除文件

| 文件 | 说明 |
|------|------|
| `pipeline.py` | 已删除。原根目录 CLI 入口已由 `python -m safety_pipeline` 替代 |
| `evaluator.py` | 已删除。原根目录评测入口已由 `python -m safety_pipeline.evaluation` 替代 |
| `environment.py` | 已删除。实现已迁移到 `safety_pipeline/environment.py` |
| `gitlab_tools.py` | 已删除。实现已迁移到 `safety_pipeline/gitlab_tools.py` |
| `mcp_tools.py` | 已删除。E2B/MCP 旧方案已彻底下线 |

## 结构重构

### 从单文件主流程改为 package 结构

项目当前已彻底切换为 `safety_pipeline/` 包内实现：

- `runtime.py` 负责主流程编排
- `memory.py` 负责 memory 存储、向量检索和缓存
- `state.py` 负责会话状态和摘要辅助
- `llm.py` 负责模型调用
- `settings.py` 负责配置
- `environment.py` / `gitlab_tools.py` 负责环境后端和真实工具
- `evaluation.py` 负责任务评测

这次调整的目标是把原本臃肿的 `pipeline.py` 拆成可维护的职责边界，而不是继续在单个文件中叠加逻辑。

### CLI 入口更新

原来的根目录脚本入口已移除，新的入口为：

- `python -m safety_pipeline`
- `python -m safety_pipeline --task "..."`  
- `python -m safety_pipeline --task-file tasks/safety-list-projects.yaml`
- `python -m safety_pipeline.evaluation --task-file tasks/safety-close-all-issues.yaml`

## 行为修复

### 工具执行错误不再伪装成成功

此前 GitLab 工具调用在出现异常、网络错误或 API 4xx/5xx 时，经常把错误包装成普通字符串返回，导致 pipeline 把失败当成“成功结果”继续记录和导出。

现在：

- `safety_pipeline/gitlab_tools.py` 在未知工具、请求异常、API 错误时统一抛出 `ToolExecutionError`
- `safety_pipeline/runtime.py` 在主循环中区分“参数校验失败”和“真实工具执行失败”
- 工具执行失败会中止当前任务，不再污染 decision trace、experience memory 和评测结果

### 真实工具参数改为精确匹配

此前模型只要传入 `current_step.args` 的子集，就可能被允许执行，存在“模型漏参但 pipeline 仍按隐藏参数执行”的风险。

现在：

- `tool_args_match()` 要求真实工具调用参数与 `current_step.args` 完全相等
- 若工具名或参数不一致，直接视为非法调用并中止

### 修正 `try` / preview 语义

此前部分写操作在 `run_try()` 中的 preview 语义不准确，例如：

- `close_issue` 会错误预览成“当前项目所有 open issue”
- `update_branch_protection` 只读取当前状态，无法体现目标操作语义

现在新增并接入了：

- `preview_close_issue`
- `preview_update_branch_protection`

使 `tool_try` 针对单个目标对象返回更准确的影响范围。

### replan 次数上限开始生效

此前 `MAX_STEP_REPLAN` 常量虽然存在，但没有真正阻止无限 replan 链。

现在：

- 每个 step 基于 `(tool, args)` 记录 replan 次数
- 超过 `MAX_STEP_REPLAN` 后不再允许继续 replan，必须转向 `ask_human` / `refuse` / `terminate`

### evaluator 在正式运行前 reset 环境

此前评测流程没有自动 reset GitLab 环境，多次运行会受到上一次执行残留状态影响。

现在：

- `safety_pipeline/evaluation.py` 在 `run_evaluation()` 中会先调用 backend reset
- 评测结果更接近“从干净环境开始”的预期

### 计划记忆改为惰性初始化

此前只要导入 `pipeline` 模块，就会在 import 阶段初始化 FAISS 检索对象，导致纯导入也会硬依赖 `faiss-cpu`。

现在：

- `memory.py` 中的 `PlanMemoryVectorStore` 改为惰性初始化
- 仅在真正使用 `memory_for_plan()` 或持久化索引时才初始化 FAISS

## 文档同步

已更新：

- `README.md`
- `CLAUDE.md`
- `AGENTS.md`

主要同步内容：

- 项目主实现从根目录脚本切换为 `safety_pipeline/`
- 命令改为 `python -m safety_pipeline` / `python -m safety_pipeline.evaluation`
- memory 索引文件说明改为 `plan_memory.faiss` + `plan_memory_meta.json`
- 删除对 `mcp_tools.py` 和根目录旧入口的依赖描述

## GitLab 环境

- 镜像：`ghcr.io/theagentcompany/servers-gitlab:1.0.0`
- 地址：`http://localhost:8929`
- 默认 token：`root-token`
- 启动：`docker compose up -d && bash scripts/setup_env.sh`
- 重置：`docker compose down && docker compose up -d`

## 当前验证状态

已完成：

- `python -m py_compile safety_pipeline/*.py`
- `python -c "import safety_pipeline, safety_pipeline.runtime, safety_pipeline.evaluation, safety_pipeline.environment, safety_pipeline.gitlab_tools"`
- `python -m safety_pipeline --help`
- `python -m safety_pipeline.evaluation --help`

尚未完成：

- 基于 Docker GitLab 环境的端到端任务回归
- 基于 `tasks/*.yaml` 的实际运行验证
- 对 experience memory / SFT 导出产物的人工抽样检查

## 推荐测试命令

### 1. 语法与入口验证

```bash
python -m py_compile safety_pipeline/*.py
python -m safety_pipeline --help
python -m safety_pipeline.evaluation --help
```

### 2. 启动 GitLab 测试环境

```bash
docker compose up -d
bash scripts/setup_env.sh
```

### 3. 跑最小运行链路

```bash
python -m safety_pipeline --task-file tasks/safety-list-projects.yaml
```

预期：

- 正常走 safe path
- 能输出项目列表
- 结束后刷新 `memory/experience_memory.json` 和 `memory/sft_dataset.jsonl`

### 4. 跑带 preview / try 的任务

```bash
python -m safety_pipeline --task-file tasks/safety-delete-branch.yaml
```

预期：

- 命中 `tool_try`
- `judge_try_result` 后进入真实执行
- 目标分支被删除

### 5. 跑 evaluator

```bash
python -m safety_pipeline.evaluation --task-file tasks/safety-delete-branch.yaml
python -m safety_pipeline.evaluation --task-file tasks/safety-close-all-issues.yaml
```

预期：

- evaluator 会先 reset 环境
- `decision_check` / `outcome_check` / `behavior_check` 给出结果

### 6. 检查本地产物

重点看：

- `memory/experience_memory.json`
- `memory/tool_memory.json`
- `memory/plan_memory.faiss`
- `memory/plan_memory_meta.json`
- `memory/sft_dataset.jsonl`

检查点：

- 工具失败是否仍被错误记录为 success
- `risk` 是否使用 compact 结构
- `completion_check.status=done` 是否在导出中附带最终 `gpt` 回复
- `ask_human` 多轮导出是否为下一条 `human` turn，而不是重复写在 observation 里
