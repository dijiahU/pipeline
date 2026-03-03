# Agent Safety Pipeline

基于 E2B 云端沙箱的 Agent 安全执行框架。在 Agent 执行工具调用前，通过 **Plan 阶段风险检测** + **Execution 阶段沙箱试执行** 双重防护，拦截危险操作。

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
- LLM 评估计划的安全风险
- 有风险则 replan（最多 3 次），超次数或任务模糊则交给人工

### Execution 阶段

```
safe plan → 遍历每个 step:
  ├─ memory 命中 → 直接在新沙箱中执行
  └─ 未见过 → E2B 沙箱 tool try
                ├─ 记录执行前文件状态
                ├─ 执行 tool
                ├─ 记录执行后文件状态
                ├─ LLM 对比前后状态判定安全性
                ├─ 安全 → 存入 memory，返回结果
                └─ 不安全 → BLOCKED
```

## 文件说明

| 文件 | 说明 |
|------|------|
| `mcp_tools.py` | Tool 注册中心，所有工具的 schema + handler 统一定义 |
| `pipeline.py` | 原生 OpenAI API 版本的 pipeline |
| `pipeline_langchain.py` | LangChain/LangGraph 版本的 pipeline |

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
pip3 install -r requirements.txt
```

### 2. 配置 API Key

在代码中修改（`pipeline.py` 或 `pipeline_langchain.py`）：

```python
E2B_API_KEY = "your_e2b_api_key"      # https://e2b.dev
OPENAI_API_KEY = "your_openai_api_key" # https://platform.openai.com/api-keys
```

### 3. 运行

```bash
# 原生 OpenAI 版本
python3 pipeline.py

# LangChain/LangGraph 版本
python3 pipeline_langchain.py
```

## 两个版本的区别

| | `pipeline.py` | `pipeline_langchain.py` |
|---|---|---|
| LLM 调用 | `openai.ChatCompletion` + 手动 `json.loads` | `ChatOpenAI` + `with_structured_output` (Pydantic) |
| 流程控制 | 手动 if/else + for 循环 | LangGraph `StateGraph` + 条件边 |
| 依赖 | `openai` | `langchain-openai` + `langgraph` |
| 代码量 | 更多，但更直观 | 更少，结构更清晰 |

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

不需要修改 `pipeline.py` 或 `pipeline_langchain.py`，新 tool 会自动被发现和使用。

## 依赖

- [E2B](https://e2b.dev/) — 云端代码沙箱，提供隔离的 Linux 执行环境
- [LangChain](https://langchain.com/) / [LangGraph](https://github.com/langchain-ai/langgraph) — LLM 应用框架（LangGraph 版本使用）
- [OpenAI](https://platform.openai.com/) — GPT-4o 作为规划和安全判定的 LLM
