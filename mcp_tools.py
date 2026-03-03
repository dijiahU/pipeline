"""
MCP Tool 注册中心
- 所有 tool 统一定义在这里（schema + handler 一体）
- Pipeline 通过 call_tool(name, args) 动态调用，无需 if/elif
- 可选: 作为 MCP Server 运行 (python mcp_tools.py)
"""

import inspect
from e2b_code_interpreter import Sandbox


# ==================== Tool Registry ====================

_REGISTRY = {}   # name -> {"handler": fn, "schema": dict}
_sandbox: Sandbox = None


def set_sandbox(sandbox: Sandbox):
    """设置当前 sandbox（pipeline 每次 tool try 前调用）"""
    global _sandbox
    _sandbox = sandbox


def tool(name: str, description: str, params: dict):
    """
    装饰器: 注册一个 tool
    - name: tool 名称
    - description: tool 描述（给 LLM 看）
    - params: 参数描述 {"param_name": {"type": "string", "description": "..."}, ...}
    """
    def decorator(func):
        # 从函数签名提取 required 参数（没有默认值的）
        sig = inspect.signature(func)
        required = [
            p for p, v in sig.parameters.items()
            if v.default is inspect.Parameter.empty
        ]

        _REGISTRY[name] = {
            "handler": func,
            "schema": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": params,
                        "required": required,
                    }
                }
            },
        }
        return func
    return decorator


# ---- Pipeline 调用接口 ----

def call_tool(name: str, args: dict) -> str:
    """按名称动态调用 tool（替代 if/elif）"""
    t = _REGISTRY.get(name)
    if not t:
        return f"[错误] 未知 tool: {name}"
    try:
        return t["handler"](**args)
    except Exception as e:
        return f"[执行出错] {type(e).__name__}: {e}"


def get_all_schemas() -> list:
    """获取所有 tool 的 OpenAI function calling schema"""
    return [t["schema"] for t in _REGISTRY.values()]


def get_tool_names() -> list:
    """获取所有已注册的 tool 名称"""
    return list(_REGISTRY.keys())


# ==================== Tool Definitions ====================
# 从 agent.py 迁移过来的所有工具，每个 tool 只需定义一次

# ---- Python 代码执行 ----

@tool("run_python_code", "在沙箱中执行 Python 代码。支持数据分析、计算、文件处理等。变量和导入的模块会在会话中保持。", {
    "code": {"type": "string", "description": "要执行的 Python 代码"},
})
def run_python_code(code: str) -> str:
    result = _sandbox.run_code(code)
    outputs = []
    if result.logs and result.logs.stdout:
        outputs.extend(result.logs.stdout)
    if result.logs and result.logs.stderr:
        outputs.append(f"[stderr]: {''.join(result.logs.stderr)}")
    if result.text:
        outputs.append(result.text)
    if result.error:
        return f"错误 - {result.error.name}: {result.error.value}\n{result.error.traceback}"
    return "".join(outputs).strip() or "(执行成功，无输出)"


# ---- Shell 命令 ----

@tool("run_shell_command", "在沙箱中执行 Shell 命令。沙箱是 Linux (Ubuntu) 环境。", {
    "command": {"type": "string", "description": "要执行的 Shell 命令"},
})
def run_shell_command(command: str) -> str:
    result = _sandbox.commands.run(command, timeout=120)
    output = result.stdout or ""
    if result.stderr:
        output += f"\n[stderr]: {result.stderr}"
    if result.exit_code != 0:
        output += f"\n[退出码]: {result.exit_code}"
    return output.strip() or "(执行成功)"


# ---- 文件操作 ----

@tool("read_file", "读取沙箱中的文件内容。", {
    "file_path": {"type": "string", "description": "文件路径（如 /home/user/test.txt）"},
})
def read_file(file_path: str) -> str:
    content = _sandbox.files.read(file_path)
    return content if content else "(文件为空)"


@tool("write_file", "写入文件到沙箱。", {
    "file_path": {"type": "string", "description": "文件路径"},
    "content": {"type": "string", "description": "要写入的内容"},
})
def write_file(file_path: str, content: str) -> str:
    _sandbox.files.write(file_path, content)
    return f"文件已写入: {file_path}"


@tool("list_files", "列出目录中的文件。", {
    "directory": {"type": "string", "description": "目录路径，默认 /home/user"},
})
def list_files(directory: str = "/home/user") -> str:
    files = _sandbox.files.list(directory)
    if not files:
        return f"目录 {directory} 为空"
    lines = [f"{'[目录]' if f.type == 'dir' else '[文件]'} {f.name}" for f in files]
    return f"目录 {directory}:\n" + "\n".join(lines)


@tool("delete_file", "删除指定文件或目录。", {
    "path": {"type": "string", "description": "要删除的文件或目录路径"},
    "recursive": {"type": "boolean", "description": "是否递归删除目录"},
})
def delete_file(path: str, recursive: bool = False) -> str:
    flag = "-rf" if recursive else ""
    # 用单引号包裹 path 防止路径中包含空格或特殊字符引发错误
    result = _sandbox.commands.run(f"rm {flag} '{path}'")
    if result.exit_code == 0:
        return f"已删除: {path}"
    return f"删除失败: {result.stderr}"


@tool("send_http_request", "发送 HTTP 请求到指定 URL。", {
    "url": {"type": "string", "description": "目标 URL"},
    "method": {"type": "string", "enum": ["GET", "POST"], "description": "HTTP 方法"},
    "body": {"type": "string", "description": "请求体 (POST 时使用)"},
})
def send_http_request(url: str, method: str = "GET", body: str = "") -> str:
    import base64
    # 使用 base64 编码传递参数，彻底避免单引号/三引号导致的 Python 语法注入错误
    url_b64 = base64.b64encode(url.encode('utf-8')).decode('utf-8')
    body_b64 = base64.b64encode(body.encode('utf-8')).decode('utf-8')
    
    code = f"""
import requests, base64
url = base64.b64decode('{url_b64}').decode('utf-8')
body = base64.b64decode('{body_b64}').decode('utf-8')
if '{method.upper()}' == 'GET':
    r = requests.get(url)
else:
    r = requests.post(url, data=body.encode('utf-8'))
print(r.status_code, r.text[:500])
"""
    result = _sandbox.run_code(code)
    if result.error:
        return f"请求失败: {result.error.value}"
    return result.text or "\n".join(result.logs.stdout) or "(无返回)"


# ==================== MCP Server 模式 ====================

def run_as_mcp_server():
    """作为独立 MCP Server 运行 (python mcp_tools.py)"""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        print("需要安装: pip3 install mcp")
        return

    server = FastMCP("SafetyPipelineTools")
    for name, info in _REGISTRY.items():
        server.tool(name=name)(info["handler"])

    print(f"启动 MCP Server，已注册 {len(_REGISTRY)} 个 tools")
    server.run()


if __name__ == "__main__":
    run_as_mcp_server()
