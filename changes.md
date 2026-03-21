# Changes — experience_memory 精简 + 轨迹级召回 + SFT 导出重构

本文档记录本轮修改内容。

## 1. experience_memory 记录精简

### 删除 7 个冗余顶层字段

从 `record_experience` 中移除以下字段，它们都可以从 `flow_tool_calls` 推导：

| 删除的字段 | 原来存什么 | 现在从哪获取 |
|-----------|-----------|------------|
| `plan_memory` | 计划记忆召回结果 | `flow_tool_calls` 中 `memory_for_plan` 的 `result` |
| `risk` | 风险判断 | `flow_tool_calls` 中 `predict_risk` 的 `arguments` |
| `tool_memory` | 工具缓存查询结果 | `flow_tool_calls` 中 `memory_for_tool` 的 `result` |
| `try_result` | try 执行结果 | `flow_tool_calls` 中 `tool_try` 的 `result` |
| `try_judgment` | try 判断 | `flow_tool_calls` 中 `judge_try_result` 的 `arguments` |
| `decision_reason` | 决策理由 | risk/try_judgment 的 reasoning 字段 |
| `observed_result` | 完整 API 返回 | `dialogue_snapshot` 中的 history/results_summary |

### 精简后每条记录结构

```json
{
  "task": "...",
  "turn_id": 1,
  "step_index": 0,
  "dialogue_snapshot": { ... },
  "flow_tool_calls": [ ... ],
  "step": { ... },
  "decision": "direct_tool",
  "outcome": "try_safe_then_executed",
  "memory_id": "case-xxx"
}
```

### dialogue_snapshot 去冗余

- 移除 `initial_task` 字段（与顶层 `task` 重复）
- `known_context` 每项截断到 120 字符
- `results_summary` 每项截断到 120 字符

## 2. 全局工具调用编号

### call_index 递增

- `init_conversation_state` 新增 `tool_call_counter: 0`
- 主循环每次调用工具时 `state["tool_call_counter"] += 1`
- `build_flow_tool_call_record` 的 `call_index` 改为必填第一参数
- 编号贯穿整个会话，每调用一次工具（flow tool 或 real tool）就 +1

### 修复错误路径 flow_tool_calls 丢失

- `ToolExecutionError` 和 `RuntimeError` 路径中移除了 `clear_current_flow_tool_calls`
- 即使工具调用失败，记录也会被保留到 `record_experience` 捕获

## 3. 轨迹级 plan memory 召回

### 从 step 级改为 session 级

之前：FAISS 索引每个 step 单独一条向量，召回零散的单步记录。

现在：同一任务的所有 step 合成一条轨迹，FAISS 索引整条轨迹。

### 索引文本格式

```
task: 关闭 sotopia 项目的第 1 个 issue
tool_chain: list_projects({}) → list_issues({"project_id":"13"}) → close_issue({"project_id":"13","issue_iid":1})
step_count: 3
final_status: done
```

### 召回结果格式

```json
{
  "task_query": "...",
  "trajectories": [
    {
      "score": 0.66,
      "task": "关闭 sotopia 项目的第 1 个 issue",
      "final_status": "done",
      "tool_chain": [
        {"tool": "list_projects", "args": {}, "description": "...", "outcome": "..."},
        {"tool": "close_issue", "args": {...}, "description": "...", "outcome": "..."}
      ]
    }
  ],
  "summary": "轨迹级向量检索召回 1 条相似历史任务...",
  "retrieval_scope": "trajectory_level"
}
```

只展示真实工具调用链，不含 flow tool 细节。

## 4. SFT 导出逻辑重构

### 新数据路径

`build_export_flow_tool_calls` 直接使用 `flow_tool_calls` 记录，不再从已删除的顶层字段推断。

### 旧数据兼容

`_build_legacy_export_tool_calls` 处理没有完整 `flow_tool_calls` 的旧格式数据，从顶层 `plan_memory`/`risk`/`tool_memory` 等字段推断。

### 新增辅助函数

- `_find_recorded_call(case, tool_name)` — 从 flow_tool_calls 中找指定工具
- `_extract_risk_from_calls(case)` — 从 predict_risk 的 arguments 提取风险
- `_extract_completion_from_calls(case)` — 从 completion_check 提取状态
- `_extract_human_reply(case)` — 从 dialogue_history 提取用户回复

### 删除的旧函数

- `infer_tool_arguments`
- `infer_tool_observation`
- `build_expected_export_tool_names`
- `build_export_tool_call`
- `should_infer_export_observation`
- `is_argument_driven_control_tool`

## 5. 涉及文件

| 文件 | 改动 |
|------|------|
| `safety_pipeline/state.py` | 新增 `tool_call_counter`；`call_index` 改为必填；`build_memory_context_snapshot` 去冗余 |
| `safety_pipeline/runtime.py` | `record_experience` 精简为 4 参数；主循环传递 `call_index`；导出逻辑重构 |
| `safety_pipeline/memory.py` | `PlanMemoryVectorStore` 改为 session 级索引；`memory_for_plan` 返回轨迹 |
