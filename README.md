# Agent Safety Pipeline

基于 E2B 云端沙箱的 Agent 安全执行框架。在 Agent 执行工具调用前，通过 **Plan 阶段风险检测** + **Execution 阶段沙箱试执行** 双重防护，拦截危险操作（数据外传、文件删除、权限提升等）。

## 架构

```
用户输入 → Plan 阶段 → Execution 阶段 → 输出
              │                │
         LLM 风险检测      E2B 沙箱试执行
         replan / 人工介入   前后状态对比 → LLM 安全判定
```

### Plan 阶段

```
input → generate_plan → detect_risk
                            │
                ┌───────────┼───────────┐
                ▼                       ▼
          有风险 → replan           无风险 → safe plan
                │
          超次数/模糊 → 人工介入
```

- LLM 生成执行计划（包含 tool 名称和参数）
- LLM 评估计划的安全风险（使用认知流标签引导推理）
- 有风险则 replan（最多 3 次），超次数或任务模糊则交给人工

**认知流标签**：LLM 在风险评估时按以下顺序输出结构化推理：

| 标签 | 作用 |
|------|------|
| `<think>` | 分析计划安全性的思考过程 |
| `<memory_call>` | 结合历史风险经验进行评估 |
| `<predict>` | 预测结果：`safety risk/unsafe` 或 `without safety risk/safe` |
| `<reflect>` | 反思是否需要 replan、求助人类、或安全放行 |

### Execution 阶段

```
safe plan → 遍历每个 step:
  ├─ memory 命中 → 直接在新沙箱中执行
  └─ 未见过 → E2B 沙箱 tool try
                ├─ 记录执行前文件状态 (md5sum)
                ├─ 执行 tool
                ├─ 记录执行后文件状态 (md5sum)
                ├─ LLM 对比前后状态判定安全性
                ├─ 安全 → 存入 memory，返回结果
                └─ 不安全 → BLOCKED，升级到人工介入
```

## Memory 系统

Pipeline 包含两类 memory，用于减少重复判断：

### PlanMemory（Plan 阶段）

存储被判定为有风险的计划，作为后续 LLM 推理的参考。

- **存储时机**：plan 被 `detect_risk` 判定为 `is_risky=True` 时
- **存储内容**：`{task, plan, risk_reason}`
- **召回方式**：全量序列化为 JSON，注入 `generate_plan` 和 `detect_risk` 的 system prompt
- **无语义检索**：不管是否相关，所有历史经验都会传给 LLM

### ToolMemory（Execution 阶段）

缓存已通过沙箱验证的安全工具调用，相同调用可跳过沙箱。

- **存储时机**：tool try 在沙箱中执行后被 LLM 判定为 `is_safe=True` 时
- **存储 key**：`tool_name:json(args)`（参数排序后的签名）
- **召回方式**：精确匹配签名，命中则跳过沙箱直接执行
- **注意**：参数稍有不同就不会命中（如 `list_files({})` vs `list_files({"directory": "/home/user"})`）

### 两个版本的 Memory 差异

| | pipeline.py | pipeline_langchain.py |
|---|---|---|
| PlanMemory 作用域 | 全局对象，**跨调用共享** | State 内 list，**单次调用内共享** |
| ToolMemory 作用域 | 全局对象，**跨调用共享** | State 内 dict，**单次调用内共享** |
| 持久化 | 无（进程退出即丢失） | 无（进程退出即丢失） |

## 文件说明

| 文件 | 说明 |
|------|------|
| `pipeline.py` | 原生 OpenAI API 版本：手动 JSON 解析 + `while True` 循环控制流 |
| `pipeline_langchain.py` | LangChain/LangGraph 版本：Pydantic 结构化输出 + `StateGraph` 声明式流程 |
| `mcp_tools.py` | Tool 注册中心：所有工具的 schema + handler 统一定义，可作为独立 MCP Server 运行 |
| `example.md` | pipeline_langchain.py 的完整测试输出示例 |

## 已注册的 Tools

| Tool | 说明 |
|------|------|
| `run_python_code` | 在沙箱中执行 Python 代码 |
| `run_shell_command` | 在沙箱中执行 Shell 命令 |
| `read_file` | 读取沙箱中的文件 |
| `write_file` | 写入文件到沙箱 |
| `list_files` | 列出目录中的文件 |
| `delete_file` | 删除文件或目录 |
| `send_http_request` | 发送 HTTP 请求 |

新增 tool 只需在 `mcp_tools.py` 中添加 `@tool(...)` 装饰器，pipeline 自动识别。

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
export E2B_API_KEY="your_e2b_api_key"      # https://e2b.dev
export OPENAI_API_KEY="your_openai_api_key" # https://platform.openai.com/api-keys
```

代码通过 `os.environ.get()` 读取，不需要修改源码。

### 3. 运行

```bash
# 原生 OpenAI 版本
python pipeline.py

# LangChain/LangGraph 版本
python pipeline_langchain.py

# 作为独立 MCP Server 运行
python mcp_tools.py
```

## 两个版本的对比

| | pipeline.py | pipeline_langchain.py |
|---|---|---|
| LLM 调用 | `openai.OpenAI` + `response_format=json_object` | `ChatOpenAI` + `with_structured_output(Pydantic)` |
| 输出解析 | 手动 `json.loads()` | Pydantic 模型自动解析（`Plan`, `RiskAssessment`, `SafetyJudgment`） |
| 流程控制 | `while True` + if/else + for 循环 | LangGraph `StateGraph` + 条件边路由 |
| 人工介入 | `input()` 在主循环中 | `request_human` 节点 + `route_after_human` 路由 |
| Memory 持久性 | 全局对象，跨 `pipeline()` 调用共享 | State 内，每次 `pipeline()` 调用重新初始化 |
| 核心函数 | `plan_phase()`, `generate_plan()`, `detect_risk()`, `execution_phase()`, `tool_try_in_sandbox()`, `judge_safety()` | 6 个 Graph 节点 + 4 个路由函数 |
| 依赖 | `openai`, `e2b-code-interpreter` | `langchain-openai`, `langgraph`, `pydantic`, `e2b-code-interpreter` |

两个版本实现相同的双重验证逻辑，LangGraph 版本结构更声明式。

## 添加新 Tool

在 `mcp_tools.py` 中添加：

```python
@tool("my_new_tool", "工具描述", {
    "param1": {"type": "string", "description": "参数说明"},
})
def my_new_tool(param1: str) -> str:
    result = _sandbox.commands.run(f"some_command {param1}")
    return result.stdout
```

不需要修改 `pipeline.py` 或 `pipeline_langchain.py`，新 tool 会通过 `get_all_schemas()` 自动被发现和使用。

## 依赖

- [E2B](https://e2b.dev/) — 云端代码沙箱，提供隔离的 Linux 执行环境
- [LangChain](https://langchain.com/) / [LangGraph](https://github.com/langchain-ai/langgraph) — LLM 应用框架（LangGraph 版本使用）
- [OpenAI](https://platform.openai.com/) — GPT-4o 作为规划和安全判定的 LLM
