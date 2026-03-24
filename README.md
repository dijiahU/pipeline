# Agent Safety Pipeline

当前仓库的主实现已经集中到 [`safety_pipeline/`](./safety_pipeline) 包中。整体不再把系统描述为"整份 plan 的 safe/unsafe 阻断器"，而是一个 **decision-driven、step-level** 的安全执行 pipeline：每一轮只围绕一个"当前最小可执行 step"推进，再基于证据决定进入真实执行、沙箱试执行、重规划、向人追问或拒绝。

## 当前框架

核心链路如下：

```text
user input
  -> [auto] memory_for_plan（轨迹级相似任务召回）
  -> ask_human / refuse / predict_risk
       ├─ safe
       │   -> [auto] memory_for_tool（工具级安全记录检索）
       │      ├─ hit  -> direct_tool
       │      └─ miss -> tool_try -> judge_try_result
       │                         ├─ safe   -> direct_tool
       │                         └─ unsafe -> replan / ask_human / terminate
       └─ risky -> replan / ask_human / refuse

完成所有 step 后 -> completion_check
任务结束时 -> persist_local_artifacts
```

几个关键点：

- `memory_for_plan` 和 `memory_for_tool` 均由代码自动执行，不作为 flow tool 暴露给模型。
- `memory_for_plan` 以轨迹级（session）粒度召回相似历史任务的完整工具调用链，只展示真实工具执行，不含 flow tool 细节。
- `memory_for_tool` 按工具名（非精确参数签名）检索安全记录，返回最近 2 条匹配结果。命中则跳过 `tool_try` 直接执行，未命中则进入沙箱试执行。
- `predict_risk`、`judge_try_result`、`replan`、`completion_check` 都是参数驱动的控制工具：判断内容写在 `arguments`，observation 只返回确认和状态推进。
- `replan` 现在一次只生成一个替代 step，字段是 `arguments.new_step`，不再使用 `new_steps` 数组。
- 主循环带有 tool-call 校验失败重试；错误会写入 `last_tool_error` 反馈给模型，而不是第一次字段错误就直接崩溃。

## Memory 与导出

`safety_pipeline/` 会在本地维护三类数据：

- `memory/experience_memory.json`：逐 step 保存决策和完整 flow tool 轨迹。每条记录只保留 `task, turn_id, step_index, dialogue_snapshot, flow_tool_calls, step, decision, outcome`，所有详细信息通过 `flow_tool_calls` 获取。每个工具调用带有全局递增的 `call_index`。
- `memory/tool_memory.json`：按工具签名缓存安全调用记录，运行时按工具名级别检索（返回最近 2 条）。
- `memory/plan_memory.faiss` + `memory/plan_memory_meta.json`：基于本地 embedding + FAISS 的轨迹级语义检索索引，按 session 粒度索引，召回完整工具调用链。

每次运行结束后都会自动刷新：

- `memory/sft_dataset.jsonl`

这份数据由 experience memory 导出，便于后续做 SFT 样本整理。

当前导出是多轮 tool-calling 轨迹，顶层字段为：

- `system`：系统提示词
- `tools`：JSON 字符串，每个工具用 `{type: "function", function: {...}}` 包装，包含 flow tool 和 real tool
- `conversations`：多轮对话序列，按 LlamaFactory ShareGPT/tool-calling 格式

当前导出约定还包括：

- `memory_for_plan` 和 `memory_for_tool` 虽然运行时由代码自动执行，但 SFT 导出时以 `function_call({}) + observation` 形式注入对话，让训练数据看起来像模型主动调用的
- `ask_human` 如果收到了真实用户回复，会导出成 `function_call(ask_human) -> human(...)`，而不是在 `observation` 里重复一遍 `human_reply`
- `completion_check.status=done` 时，会在最后追加一条 `gpt`，内容来自 `reply`
- 样本目标格式与 LLaMAFactory 的 ShareGPT/tool-calling 交替规则保持一致

## 运行方式

安装依赖：

```bash
pip install -r requirements.txt
```

配置环境变量（在项目根目录创建 `.env` 文件，会被自动加载）：

```
OPENAI_API_KEY=your_openai_api_key
OPENAI_BASE_URL=https://openrouter.ai/api/v1   # 可选，支持 OpenRouter/DeepSeek 等兼容 API
OPENAI_MODEL=openai/gpt-4o                       # 可选，默认 gpt-5.2
GITLAB_BASE_URL=http://localhost:8929
GITLAB_ACCESS_TOKEN=root-token
```

启动 GitLab 测试环境：

```bash
docker compose up -d && bash scripts/setup_env.sh
```

一键重置 GitLab 环境（恢复初始数据 + 续期 token）：

```bash
bash scripts/reset_env.sh
```

运行默认任务：

```bash
python -m safety_pipeline
```

运行自定义任务：

```bash
python -m safety_pipeline --task "列出所有 GitLab 项目"
```

从 YAML 文件加载任务：

```bash
python -m safety_pipeline --task-file tasks/safety-list-projects.yaml
```

运行评测：

```bash
python -m safety_pipeline.evaluation --task-file tasks/safety-close-all-issues.yaml
```

快速做语法检查：

```bash
python -m py_compile safety_pipeline/*.py
```

## 文件说明

- `safety_pipeline/runtime.py`：主流程编排，包含 flow-control、参数校验、tool-call 重试、执行分发和导出主逻辑。
- `safety_pipeline/gitlab_tools.py`：GitLab API 工具模块，使用 `@gitlab_tool()` 装饰器注册。
- `safety_pipeline/environment.py`：环境后端抽象层，定义 `EnvironmentBackend` 和 `GitLabBackend`；工厂函数 `get_backend()` 按名称返回单例。
- `safety_pipeline/evaluation.py`：任务级评测框架，支持 decision_check / outcome_check / behavior_check 三种检查。
- `safety_pipeline/memory.py`：经验记忆、工具缓存、FAISS 检索与 memory 相关辅助。
- `safety_pipeline/state.py`：会话状态、摘要、风险记录与对话更新辅助。
- `safety_pipeline/llm.py`：OpenAI 客户端与模型调用封装。
- `safety_pipeline/settings.py`：运行时常量和路径配置。
- `tasks/*.yaml`：评测任务定义文件，包含用户任务、oracle 预期和 NPC 场景配置。
- `docker-compose.yml`：GitLab 测试环境的 Docker compose 配置。
- `scripts/setup_env.sh`：容器启动和健康检查脚本。
- `criterion.md`：当前流程控制标准。
- `branches.md`：SFT 分支枚举示例。

## 当前分流语义

- `replan`：问题在方案本身过猛，但 agent 可以自行改写成更可控步骤。
- `ask_human`：继续安全完成任务必须依赖用户提供的信息、确认或授权。
- `refuse`：任务目标本身恶意、越权、外传、破坏或不应执行。
- `terminate`：`tool_try` 已暴露不可接受副作用，且任务无法在安全边界内继续推进。

如果你要修改当前实现，优先保持 `safety_pipeline/runtime.py` 里的这条固定链路，而不是回退到旧的 plan-level safe/unsafe 描述。

> 查看上一次修改详情：[changes.md](./changes.md)
