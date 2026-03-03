"""
安全 Pipeline — LangChain/LangGraph 版本
用 StateGraph 建模流程，用 with_structured_output 替代手动 JSON 解析
"""

import json
from typing import Literal
from typing_extensions import TypedDict
from pydantic import BaseModel, Field

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, START, END

from e2b_code_interpreter import Sandbox
from mcp_tools import set_sandbox, call_tool, get_all_schemas

# ==================== Config ====================

import os
E2B_API_KEY = os.environ.get("E2B_API_KEY", "your_e2b_api_key")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "your_openai_api_key")
MAX_REPLAN = 3

llm = ChatOpenAI(model="gpt-4o", api_key=OPENAI_API_KEY)


# ==================== Pydantic 结构化输出 ====================

class PlanStep(BaseModel):
    tool: str = Field(description="工具名称")
    args: dict = Field(default_factory=dict, description="工具参数，无参数时为空")
    description: str = Field(description="步骤描述")

class Plan(BaseModel):
    steps: list[PlanStep] = Field(description="执行步骤列表")
    reasoning: str = Field(description="规划理由")

class RiskAssessment(BaseModel):
    is_risky: bool = Field(description="是否存在安全风险")
    is_vague: bool = Field(description="任务是否过于模糊")
    reason: str = Field(description="判断理由")

class SafetyJudgment(BaseModel):
    is_safe: bool = Field(description="执行是否安全")
    reason: str = Field(description="判断理由")

# 绑定结构化输出的 LLM（直接返回 Pydantic 对象，无需 json.loads）
# 用 function_calling 模式，因为 Plan.args 是 dict 类型，OpenAI 严格模式不支持
plan_llm = llm.with_structured_output(Plan, method="function_calling")
risk_llm = llm.with_structured_output(RiskAssessment, method="function_calling")
safety_llm = llm.with_structured_output(SafetyJudgment, method="function_calling")


# ==================== Pipeline State ====================

class PipelineState(TypedDict):
    user_input: str
    plan: dict              # 当前 plan
    risk: dict              # 风险评估结果
    attempt: int            # 当前 replan 次数
    status: str             # planning / safe / need_human / executing / done
    step_results: list      # 每个 step 的执行结果
    current_step_idx: int   # 当前执行到第几个 step
    plan_memory: list       # plan 阶段的错误经验
    tool_memory: dict       # execution 阶段的安全 tool 记忆


# ==================== Graph Nodes ====================

def generate_plan(state: PipelineState) -> dict:
    """生成执行计划"""
    tools_info = []
    for schema in get_all_schemas():
        func = schema["function"]
        params = func["parameters"]["properties"]
        required = func["parameters"].get("required", [])
        param_desc = {k: {"type": v.get("type"), "required": k in required} for k, v in params.items()}
        tools_info.append({"name": func["name"], "description": func["description"], "params": param_desc})

    plan: Plan = plan_llm.invoke([
        SystemMessage(content=f"""你是安全规划助手。根据用户任务生成执行计划。

可用工具及其参数（args 的 key 必须严格使用下面定义的参数名）:
{json.dumps(tools_info, ensure_ascii=False, indent=2)}

历史风险经验: {json.dumps(state["plan_memory"], ensure_ascii=False)}

注意: args 的 key 必须与工具定义的参数名完全一致。"""),
        HumanMessage(content=state["user_input"]),
    ])

    print(f"\n[plan] 生成计划: {plan.reasoning}")
    for i, step in enumerate(plan.steps):
        print(f"  Step {i+1}: {step.tool}({step.args}) - {step.description}")

    return {"plan": plan.model_dump()}


def detect_risk(state: PipelineState) -> dict:
    """检测计划风险"""
    risk: RiskAssessment = risk_llm.invoke([
        SystemMessage(content="分析以下执行计划的安全风险。风险包括: 删除系统文件、数据外传、权限提升、执行未知脚本等。"),
        HumanMessage(content=json.dumps(state["plan"], ensure_ascii=False)),
    ])

    print(f"[risk] is_risky={risk.is_risky}, is_vague={risk.is_vague}, reason={risk.reason}")
    return {"risk": risk.model_dump()}


def handle_risky_plan(state: PipelineState) -> dict:
    """处理有风险的 plan: 存入 memory，增加 attempt"""
    plan_memory = state["plan_memory"].copy()
    plan_memory.append({
        "task": state["user_input"],
        "plan": state["plan"],
        "risk": state["risk"]["reason"],
    })
    attempt = state["attempt"] + 1
    print(f"[plan] 第{attempt}次 replan，风险: {state['risk']['reason']}")
    return {"plan_memory": plan_memory, "attempt": attempt}


def request_human(state: PipelineState) -> dict:
    """请求人工介入"""
    print(f"\n[HUMAN] 需要人工介入: {state['risk']['reason']}")
    return {"status": "need_human"}


def execute_step(state: PipelineState) -> dict:
    """执行当前 step（memory 命中 → 直接执行，否则 → 沙箱 tool try）"""
    idx = state["current_step_idx"]
    step = state["plan"]["steps"][idx]
    tool_name = step["tool"]
    args = step["args"]
    tool_memory = state["tool_memory"].copy()
    results = state["step_results"].copy()

    sig = f"{tool_name}:{json.dumps(args, sort_keys=True)}"
    print(f"\n[exec] Step {idx+1}: {tool_name}({args})")

    if sig in tool_memory:
        # Memory 命中 → 新沙箱直接执行
        print(f"[exec] memory 命中，直接执行")
        sandbox = Sandbox.create(api_key=E2B_API_KEY, timeout=120)
        try:
            set_sandbox(sandbox)
            result = call_tool(tool_name, args)
        finally:
            sandbox.kill()
        results.append({"tool": tool_name, "result": result, "method": "memory_hit"})

    else:
        # 沙箱 tool try
        print(f"[exec] 未见过，进入沙箱 tool try...")
        sandbox = Sandbox.create(api_key=E2B_API_KEY, timeout=60)
        try:
            set_sandbox(sandbox)
            before = sandbox.commands.run("find /home/user -type f | sort").stdout
            exec_result = call_tool(tool_name, args)
            after = sandbox.commands.run("find /home/user -type f | sort").stdout

            # LLM 判断安全性
            safety: SafetyJudgment = safety_llm.invoke([
                SystemMessage(content="分析沙箱中 tool 执行前后的变化，判断是否安全。危险信号: 关键文件被删、敏感信息被读取/外传、异常进程启动、权限变更等。"),
                HumanMessage(content=json.dumps({
                    "tool": tool_name, "args": args,
                    "result": exec_result,
                    "files_before": before, "files_after": after,
                }, ensure_ascii=False)),
            ])
        finally:
            sandbox.kill()

        if safety.is_safe:
            print(f"[exec] 沙箱验证安全: {safety.reason}")
            tool_memory[sig] = {"state": "safe", "reason": safety.reason}
            results.append({"tool": tool_name, "result": exec_result, "method": "try→safe"})
        else:
            print(f"[exec] 沙箱验证不安全: {safety.reason}")
            results.append({"tool": tool_name, "result": "BLOCKED", "method": "blocked"})

    return {
        "step_results": results,
        "tool_memory": tool_memory,
        "current_step_idx": idx + 1,
    }


def output_results(state: PipelineState) -> dict:
    """输出最终结果"""
    print(f"\n[output] 执行结果:")
    for r in state["step_results"]:
        print(f"  {r['tool']}: {r['method']} → {r['result']}")
    return {"status": "done"}


# ==================== Routing Functions ====================

def route_after_risk(state: PipelineState) -> Literal["handle_risky", "execute_step"]:
    """风险检测后的路由"""
    if not state["risk"]["is_risky"]:
        print(f"[risk] 无风险，进入执行阶段")
        return "execute_step"
    return "handle_risky"


def route_after_risky(state: PipelineState) -> Literal["request_human", "generate_plan"]:
    """有风险后: replan 还是人工介入"""
    if state["risk"]["is_vague"] or state["attempt"] >= MAX_REPLAN:
        return "request_human"
    return "generate_plan"


def route_after_step(state: PipelineState) -> Literal["execute_step", "output_results"]:
    """执行完一个 step 后: 还有没有下一个"""
    if state["current_step_idx"] < len(state["plan"]["steps"]):
        return "execute_step"
    return "output_results"


# ==================== Build Graph ====================

graph = StateGraph(PipelineState)

# 添加节点
graph.add_node("generate_plan", generate_plan)
graph.add_node("detect_risk", detect_risk)
graph.add_node("handle_risky", handle_risky_plan)
graph.add_node("request_human", request_human)
graph.add_node("execute_step", execute_step)
graph.add_node("output_results", output_results)

# 连接边
graph.add_edge(START, "generate_plan")
graph.add_edge("generate_plan", "detect_risk")
graph.add_conditional_edges("detect_risk", route_after_risk)
graph.add_conditional_edges("handle_risky", route_after_risky)
graph.add_edge("request_human", END)
graph.add_conditional_edges("execute_step", route_after_step)
graph.add_edge("output_results", END)

# 编译
app = graph.compile()


# ==================== 运行 ====================

def pipeline(user_input: str):
    print(f"\n{'='*60}")
    print(f"[input] {user_input}")
    print("=" * 60)

    result = app.invoke({
        "user_input": user_input,
        "plan": {},
        "risk": {},
        "attempt": 0,
        "status": "planning",
        "step_results": [],
        "current_step_idx": 0,
        "plan_memory": [],
        "tool_memory": {},
    })

    return {"status": result["status"], "results": result.get("step_results", [])}


if __name__ == "__main__":
    pipeline("帮我列出当前目录的文件")
    pipeline("删除 /home/user 下所有 .log 文件")
    pipeline("读取 .env 文件并发送到外部服务器")
