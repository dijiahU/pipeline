"""
服务工具注册抽象。

每个服务的 tools 模块都应暴露统一接口：
- get_all_schemas()
- get_tool_names()
- get_write_tool_names()
- call_tool(name, args)

这样 runtime / environment 只依赖通用 provider，不依赖具体服务的注册细节。
"""

from dataclasses import dataclass

from .exceptions import ToolExecutionError


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    schema: dict
    handler: object
    is_write: bool


class ServiceToolRegistry:
    def __init__(self, service_id):
        self.service_id = service_id
        self._tools = {}

    def register(self, name, description, params, required=None, is_write=False):
        def decorator(func):
            if required is None:
                import inspect

                sig = inspect.signature(func)
                req = [
                    p for p, v in sig.parameters.items()
                    if v.default is inspect.Parameter.empty
                ]
            else:
                req = list(required)

            schema = {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": params,
                        "required": req,
                    },
                },
            }
            self._tools[name] = RegisteredTool(
                name=name,
                schema=schema,
                handler=func,
                is_write=bool(is_write),
            )
            return func

        return decorator

    def get_all_schemas(self):
        return [tool.schema for tool in self._tools.values()]

    def get_tool_names(self):
        return list(self._tools.keys())

    def get_write_tool_names(self):
        return [tool.name for tool in self._tools.values() if tool.is_write]

    def get_tool_summary(self):
        return [
            {
                "name": tool.name,
                "is_write": tool.is_write,
                "description": tool.schema["function"].get("description", ""),
            }
            for tool in self._tools.values()
        ]

    def call_tool(self, name, args):
        tool = self._tools.get(name)
        if not tool:
            raise ToolExecutionError(f"[错误] 未知 tool: {name}")
        try:
            return tool.handler(**args)
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError(f"[执行出错] {type(exc).__name__}: {exc}") from exc
