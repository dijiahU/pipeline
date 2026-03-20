# Agent Safety Pipeline

当前仓库的主实现是 [`pipeline.py`](./pipeline.py)。它不再把系统描述为“整份 plan 的 safe/unsafe 阻断器”，而是一个 **decision-driven、step-level** 的安全执行 pipeline：每一轮只围绕一个“当前最小可执行 step”推进，再基于证据决定进入真实执行、沙箱试执行、重规划、向人追问或拒绝。

## 当前框架

核心链路如下：

```text
user input
  -> ask_human / refuse / memory_for_plan
  -> predict_risk
       ├─ safe
       │   -> memory_for_tool
       │      ├─ hit  -> direct_tool
       │      └─ miss -> tool_try -> judge_try_result
       │                         ├─ safe   -> direct_tool
       │                         └─ unsafe -> replan / ask_human / terminate
       └─ risky -> replan / ask_human / refuse

完成所有 step 后 -> completion_check
任务结束时 -> persist_local_artifacts
```

几个关键点：

- 第一轮只能走 `memory_for_plan / ask_human / refuse`，不再显式暴露 `thinking_step`。
- `memory_for_plan` 现在要求显式提交 `tool + tool_args + description`，用于形成当前 step；它的 memory 检索以“当前用户任务”相似度为主，而不是只按 step 相似度召回。
- `predict_risk`、`judge_try_result`、`replan`、`completion_check` 都是参数驱动的控制工具：判断内容写在 `arguments`，observation 只返回确认和状态推进。
- `replan` 现在一次只生成一个替代 step，字段是 `arguments.new_step`，不再使用 `new_steps` 数组。
- 主循环带有 tool-call 校验失败重试；错误会写入 `last_tool_error` 反馈给模型，而不是第一次字段错误就直接崩溃。

## Memory 与导出

`pipeline.py` 会在本地维护三类数据：

- `memory/experience_memory.json`：逐 step 保存决策、理由、结果和 flow tool 轨迹；新 case 使用更扁平的 `risk = {level, reason, next_action, criteria}`。
- `memory/tool_memory.json`：缓存完全相同签名的安全工具调用。
- `memory/plan_memory_index.json`：基于 OpenAI embedding 的任务级语义检索索引。

每次运行结束后都会自动刷新：

- `memory/sft_dataset.jsonl`

这份数据由 experience memory 导出，便于后续做 SFT 样本整理。

当前导出是多轮 tool-calling 轨迹，顶层字段为：

- `system`
- `tool_groups`
- `tools`
- `conversations`

其中：

- `tool_groups.shared_flow_tools`：共享流程工具，如 `memory_for_plan`、`predict_risk`、`ask_human`、`completion_check`
- `tool_groups.task_tools`：当前任务实际用到的真实工具，如 `list_files`、`read_file`、`delete_file`
- `tools`：上面两组的扁平合并，便于直接交给训练框架

当前导出约定还包括：

- `ask_human` 如果收到了真实用户回复，会导出成 `function_call(ask_human) -> human(...)`，而不是在 `observation` 里重复一遍 `human_reply`
- `completion_check.status=done` 时，会在最后追加一条 `gpt`，内容来自 `reply`
- 样本目标格式与 LLaMAFactory 的 ShareGPT/tool-calling 交替规则保持一致

## 运行方式

安装依赖：

```bash
pip install -r requirements.txt
```

配置环境变量：

```bash
export OPENAI_API_KEY="your_openai_api_key"
export E2B_API_KEY="your_e2b_api_key"
```

运行默认任务：

```bash
python pipeline.py
```

运行自定义任务：

```bash
python pipeline.py --task "帮我检查 /home/user/app.log 最近的错误"
```

快速做语法检查：

```bash
python -m py_compile pipeline.py mcp_tools.py
```

## 文件说明

- `pipeline.py`：当前主流程，包含 flow-control、参数校验、tool-call 重试、沙箱 try、memory 持久化和 SFT 导出。
- `mcp_tools.py`：真实工具注册中心；`pipeline.py` 会动态读取这里的 tool schema 并执行工具。
- `criterion.md`：当前流程控制标准。
- `branches.md`：SFT 分支枚举示例。

## 当前分流语义

- `replan`：问题在方案本身过猛，但 agent 可以自行改写成更可控步骤。
- `ask_human`：继续安全完成任务必须依赖用户提供的信息、确认或授权。
- `refuse`：任务目标本身恶意、越权、外传、破坏或不应执行。
- `terminate`：`tool_try` 已暴露不可接受副作用，且任务无法在安全边界内继续推进。

如果你要修改当前实现，优先保持 `pipeline.py` 的这条固定链路，而不是回退到旧的 plan-level safe/unsafe 描述。
