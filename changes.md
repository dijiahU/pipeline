# Changes — memory_for_tool 自动执行 + 工具级匹配 + SFT 导出补全

本文档记录本轮修改内容。

## 1. memory_for_tool 改为自动执行

### 改前

`memory_for_tool` 作为 flow tool 暴露给模型，模型在 `predict_risk(safe)` 后显式调用它：

```
predict_risk(safe) → [LLM调用] memory_for_tool() → hit: direct_tool / miss: tool_try
```

### 改后

和 `memory_for_plan` 一样，改为代码自动执行，不再需要模型显式调用：

```
predict_risk(safe) → [代码自动] memory_for_tool() → hit: direct_tool / miss: tool_try
```

### 具体改动

- `flow_tool_predict_risk()` 中 `result=safe` 时自动调用 `memory_for_tool(step["tool"])`
- 删除 `flow_tool_memory_for_tool()` 函数
- 删除 `FLOW_TOOL_SCHEMAS["memory_for_tool"]` 条目
- 删除 `build_available_tool_schemas` 中 `need_tool_memory` 分支
- 删除 `dispatch_tool_call` 中 `memory_for_tool` 分支
- 自动执行结果记录到 `flow_tool_calls` 中，`phase=auto_tool_memory`

## 2. 匹配粒度从参数级别改为工具级别

### 改前

`memory_for_tool(tool_name, args)` 使用 `tool_signature(tool_name, args)` 做精确签名匹配，只返回 1 条或 None。

### 改后

`memory_for_tool(tool_name)` 只按工具名检索，返回该工具最近 2 条安全调用记录。

### 具体改动

- `ToolMemory` 新增 `get_safe_cases_by_tool(tool_name, top_k=2)` 方法
- `memory_for_tool()` 改为只接受 `tool_name`，返回 `safe_cases` 列表
- `sanitize_tool_memory_result()` 兼容新格式（`safe_cases` 列表）和旧格式（`safe_case` 单条）

### 返回格式

```json
{
  "hit": true,
  "safe_cases": [
    {"tool": "list_projects", "args": {}, "state": "safe", "safety_reason": "..."}
  ],
  "summary": "找到 1 条 list_projects 的安全调用记录。"
}
```

## 3. SFT 导出时补全 memory_for_plan 和 memory_for_tool 调用过程

虽然运行时是代码自动执行的，但导出 SFT 数据时，让它看起来像模型主动调用了这两个工具。

### 改动

- `build_conversations()` 和 `experience_step_to_sft_record()` 中：
  - `memory_for_plan` 以 `function_call({}) + observation` 形式注入到首条 human 消息之后
  - `memory_for_tool` 在 `predict_risk(safe)` 的 observation 之后以 `function_call({}) + observation` 注入
  - human 消息不再嵌入 `[plan_memory]` 文本，只包含纯任务内容
- `build_tool_schema_map()` 保留 `memory_for_plan` 和 `memory_for_tool` 的 schema
- `build_export_tool_groups()` 在 SFT tools 列表中始终包含这两个 schema
- `should_export_flow_tool()` 排除 `memory_for_tool`，避免从 `flow_tool_calls` 重复导出

### SFT 导出对话序列示例

```
[human] 列出 GitLab 上所有项目
[function_call] memory_for_plan({})
[observation] {trajectories: [...], summary: "..."}
[function_call] predict_risk({tool: "list_projects", result: "safe", ...})
[observation] {accepted: true, ...}
[function_call] memory_for_tool({})          ← 代码注入
[observation] {hit: true, safe_cases: [...]}
[function_call] direct_tool({})
[observation] {tool: "list_projects", exec_result: ...}
[function_call] completion_check({status: "done", ...})
[observation] {accepted: true, ...}
[gpt] 以下是 GitLab 上的所有项目：...
```

## 4. System prompt 更新

- `SFT_TOOLCALL_SYSTEM_PROMPT`：说明 `memory_for_plan` 和 `memory_for_tool` 的调用流程
- `TOOL_AGENT_SYSTEM_PROMPT`：说明 safe 路径下系统自动查询工具记忆，结果在 snapshot 中

## 5. 涉及文件

| 文件 | 改动 |
|------|------|
| `safety_pipeline/memory.py` | 新增 `get_safe_cases_by_tool()`；`memory_for_tool()` 改为工具级匹配；`sanitize_tool_memory_result()` 兼容新旧格式 |
| `safety_pipeline/runtime.py` | 移除 `need_tool_memory` phase；自动执行 memory_for_tool；SFT 导出注入 function_call；system prompt 更新 |
