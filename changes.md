# Changes — SFT 导出格式对齐 v5 手写样本

本文档记录本轮修改内容。基于 v5 手写样本（`1_1.json` ~ `5_1.json`）的对比分析，将 pipeline 自动导出的 SFT 数据向 v5 格式靠拢。

## 1. 移除 completion_check 工具

### 改前

任务执行完毕后，模型必须调用 `completion_check({status: "done", reply: "...", reason: "..."})` 来结束任务。这是冗余的——模型应该自己知道什么时候结束。

### 改后

引入 `need_next_or_done` 阶段，使用 `tool_choice="auto"`：模型输出文本 = 任务完成，输出 tool call = 继续下一步。

### 具体改动

- 删除 `validate_completion_check_args()`、`flow_tool_completion_check()`
- 删除 `FLOW_TOOL_SCHEMAS["completion_check"]`
- 删除 `need_completion` / `need_completion_followup` phase
- 新增 `need_next_or_done` phase，使用 `call_auto_tool_choice()`
- `llm.py` 新增 `call_auto_tool_choice()` 函数
- `state.py` 移除 `current_completion` / `pending_completion_question`，新增 `final_reply`
- `should_export_flow_tool()` 过滤 `completion_check`，旧数据中的记录在导出时跳过

## 2. direct_tool({}) 替换为 act(function_name, function_arguments, execution_basis)

### 改前

导出的真实工具执行为 `direct_tool({})`，零信息量——模型学不到任何东西。

### 改后

导出时统一转为 `act`，三层结构清晰分离：

```json
{
  "name": "act",
  "arguments": {
    "function_name": "list_projects",
    "function_arguments": {"per_page": 20},
    "execution_basis": {
      "risk_level": "safe",
      "memory_match": "miss",
      "justification": "risk=safe, memory=miss, sandbox_result=safe。只读操作。"
    }
  }
}
```

### 具体改动

- 新增 `_build_act_schema()`（仅 SFT 导出使用）
- 新增 `_build_execution_basis()`：从 risk、memory、try 中聚合决策链
- 新增 `_enrich_direct_tool_for_export()`：将 `direct_tool` / 真实工具名转为 `act`
- `build_export_tool_groups()` 注入 `act` schema

## 3. 补充空参数工具调用的 SFT 导出

### 改前

`memory_for_plan({})`、`memory_for_tool({})`、`tool_try({})` 在导出时参数全空，模型无法学习调用模式。

### 改后

导出时从 case 上下文中提取实际参数：

| 工具 | 补充的参数 | 数据来源 |
|------|-----------|---------|
| `memory_for_plan` | `task_summary`, `known_context` | case.task, dialogue_snapshot.known_context |
| `memory_for_tool` | `function_name`, `function_arguments` | predict_risk.tool, predict_risk.tool_args |
| `tool_try` | `function_name`, `function_arguments` | predict_risk.tool, predict_risk.tool_args |

### 具体改动

- Schema 更新：`build_memory_for_plan_schema()` 增加 `task_summary` + `known_context` 参数
- Schema 更新：`_build_memory_for_tool_schema()` 增加 `function_name` + `function_arguments` 参数
- Schema 更新：`tool_try` 增加 `function_name` + `function_arguments` 参数
- 新增 `_enrich_memory_for_plan_args()`、`_enrich_memory_for_tool_args()`、`_enrich_tool_try_args()`
- `build_conversations()` 和 `experience_step_to_sft_record()` 中注入补充后的参数

## 4. predict_risk.reasoning 描述增强

### 改前

```
"reasoning": "对当前 step 的简短风险判断。"
```

### 改后

```
"reasoning": "对当前 step 的风险判断。需包含：1) 判断依据；2) 该操作的安全边界约束（如仅影响哪些对象、不能触及什么）。"
```

引导模型在 reasoning 中输出 constraints，不再作为单独字段，避免和 reasoning 重复。

## 5. judge_try_result.reasoning 描述增强

### 改前

```
"reasoning": "对 try 结果的简短判断。"
```

### 改后

```
"reasoning": "基于 sandbox 观察到的具体事实（如影响范围、状态变化、异常输出），判断执行结果是否安全。"
```

引导模型引用 sandbox 的具体观察结果，对标 v5 样本中 `judge_try_result` 的 reasoning 风格。

## 6. replan.reasoning 描述增强

### 改前

```
"reasoning": "为什么要改写当前 step。"
```

### 改后

```
"reasoning": "为什么要改写当前 step，以及新方案为什么比原方案更安全（需说明消除了哪些风险）。"
```

对标 v5 样本中 `replan` 的 `why_safer` 字段，通过 reasoning description 引导模型做比较性安全推理。

## 7. gold_path / case_type 元数据

SFT 导出的 `meta` 中新增：

- `gold_path`：从 `flow_tool_calls` 中提取的实际决策路径
- `case_type`：从 decision/outcome 序列推导的分类标签（如 `safe_memory_hit`、`safe_try_execute`、`replan_then_safe_try_execute`）

用于数据管理和训练样本筛选，不直接参与训练。

## 8. SFT 导出对话序列示例（更新后）

```
[human] 删除 sotopia 项目的 feature-test 分支
[function_call] memory_for_plan({"task_summary": "删除 sotopia 项目的 feature-test 分支", "known_context": [...]})
[observation] {trajectories: [...], summary: "..."}
[function_call] predict_risk({tool: "delete_branch", tool_args: {project: "sotopia", branch: "feature-test"}, result: "safe", reasoning: "..."})
[observation] {accepted: true, ...}
[function_call] memory_for_tool({"function_name": "delete_branch", "function_arguments": {"project": "sotopia", "branch": "feature-test"}})
[observation] {hit: false, ...}
[function_call] tool_try({"function_name": "delete_branch", "function_arguments": {"project": "sotopia", "branch": "feature-test"}})
[observation] {exec_status: "success", ...}
[function_call] judge_try_result({result: "safe", reasoning: "sandbox 显示仅删除了 feature-test 分支，未影响其他分支..."})
[observation] {accepted: true, ...}
[function_call] act({"function_name": "delete_branch", "function_arguments": {"project": "sotopia", "branch": "feature-test"}, "execution_basis": {"risk_level": "safe", "memory_match": "miss", "justification": "..."}})
[observation] {status: "deleted", ...}
[gpt] 已成功删除 sotopia 项目的 feature-test 分支。
```

## 9. 涉及文件

| 文件 | 改动 |
|------|------|
| `safety_pipeline/runtime.py` | 移除 completion_check；新增 act/enrichment 导出逻辑；更新 schema descriptions；新增 gold_path/case_type |
| `safety_pipeline/llm.py` | 新增 `call_auto_tool_choice()` |
| `safety_pipeline/state.py` | 移除 `current_completion`/`pending_completion_question`，新增 `final_reply` |
| `safety_pipeline/evaluation.py` | 移除 completion_check 相关引用 |
