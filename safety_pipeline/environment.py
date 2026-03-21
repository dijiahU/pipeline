"""
EnvironmentBackend 抽象 + 具体后端实现

flow tools 不变，只替换 real tools 和执行后端。
runtime 模块通过 backend 接口调用工具，不再直接依赖具体服务模块。

当前后端：GitLabBackend
未来后端：RocketChatBackend、FilesystemBackend 等，按同样模式接入。
"""

import json
import os
from abc import ABC, abstractmethod

try:
    import requests as _requests_mod
except ModuleNotFoundError:
    _requests_mod = None


# ==================== 抽象接口 ====================


class EnvironmentBackend(ABC):
    """所有环境后端的公共接口"""

    @abstractmethod
    def get_tool_schemas(self):
        """返回 OpenAI function calling 格式的 tool schema 列表"""

    @abstractmethod
    def get_tool_names(self):
        """返回所有已注册的 tool 名称列表"""

    @abstractmethod
    def execute_tool(self, name, args):
        """真实执行工具，返回结果字符串"""

    @abstractmethod
    def run_try(self, name, args):
        """try 执行，返回 {"summary": dict, "exec_result_raw": str}"""

    @abstractmethod
    def reset(self):
        """episode 间重置环境"""


# ==================== GitLabBackend ====================

# 写操作工具 -> preview 工具的映射
_GITLAB_PREVIEW_MAP = {
    "delete_branch": {
        "preview_tool": "preview_delete_branches",
        "args_transform": lambda args: {
            "project_id": args.get("project_id", ""),
            "branch_pattern": args.get("branch_name", ""),
        },
    },
    "close_issue": {
        "preview_tool": "preview_close_issue",
        "args_transform": lambda args: {
            "project_id": args.get("project_id", ""),
            "issue_iid": args.get("issue_iid"),
        },
    },
    "update_branch_protection": {
        "preview_tool": "preview_update_branch_protection",
        "args_transform": lambda args: {
            "project_id": args.get("project_id", ""),
            "branch_name": args.get("branch_name", ""),
            "action": args.get("action", ""),
        },
    },
}


class GitLabBackend(EnvironmentBackend):
    """GitLab API 后端"""

    def __init__(self):
        self._gitlab_tools = None

    def _get_gitlab_tools(self):
        if self._gitlab_tools is not None:
            return self._gitlab_tools
        try:
            from . import gitlab_tools as gitlab_tools_module
        except ModuleNotFoundError as exc:
            raise RuntimeError("当前环境缺少 gitlab_tools 模块。") from exc
        self._gitlab_tools = gitlab_tools_module
        return self._gitlab_tools

    def get_tool_schemas(self):
        return self._get_gitlab_tools().get_all_schemas()

    def get_tool_names(self):
        return self._get_gitlab_tools().get_tool_names()

    def execute_tool(self, name, args):
        return self._get_gitlab_tools().call_tool(name, args)

    def run_try(self, name, args):
        """
        GitLab try 策略：
        - 写操作工具 -> 调用对应的 preview 工具（不改状态）
        - 只读工具 -> 直接执行（无副作用）
        """
        gt = self._get_gitlab_tools()
        preview_info = _GITLAB_PREVIEW_MAP.get(name)

        if preview_info:
            # 写操作：调 preview 工具
            preview_tool = preview_info["preview_tool"]
            preview_args = preview_info["args_transform"](args)
            preview_result = gt.call_tool(preview_tool, preview_args)

            # 解析 preview 结果以构建 summary
            try:
                parsed = json.loads(preview_result) if isinstance(preview_result, str) else preview_result
            except (json.JSONDecodeError, TypeError):
                parsed = {}

            affected_count = 0
            affected_sample = []
            warnings = []
            if isinstance(parsed, dict):
                affected_count = parsed.get("total_matched", 0)
                items = parsed.get("branches", parsed.get("issues", []))
                if isinstance(items, list):
                    affected_sample = [
                        item.get("name", item.get("title", str(item)))
                        for item in items[:5]
                    ]
                warnings = parsed.get("warnings", [])

            summary = {
                "exec_status": "success",
                "state_changed": False,
                "affected_objects_count": affected_count,
                "affected_objects_sample": affected_sample,
                "unexpected_side_effect": False,
                "observed_effects": [
                    f"预览 {name}：匹配到 {affected_count} 个对象",
                ] + warnings[:3],
                "summary": f"预览 {name}：影响 {affected_count} 个对象，未执行实际操作。"
                + (f" 警告: {'; '.join(warnings)}" if warnings else ""),
            }

            return {
                "summary": summary,
                "exec_result_raw": preview_result,
            }
        else:
            # 只读工具：直接执行
            exec_result = gt.call_tool(name, args)
            summary = {
                "exec_status": "success",
                "state_changed": False,
                "affected_objects_count": 0,
                "affected_objects_sample": [],
                "unexpected_side_effect": False,
                "observed_effects": [f"只读工具 {name} 执行成功"],
                "summary": f"{name} 执行完成，无副作用。",
            }
            return {
                "summary": summary,
                "exec_result_raw": exec_result,
            }

    def reset(self):
        """调用 OpenAgentSafety 的 reset 接口重置 GitLab"""
        if _requests_mod is None:
            print("[GitLabBackend] requests 未安装，跳过 reset")
            return
        reset_base = os.environ.get("RESET_API_BASE", "http://localhost:2999")
        try:
            resp = _requests_mod.post(f"{reset_base}/api/reset-gitlab", timeout=60)
            print(f"[GitLabBackend] reset-gitlab 响应: {resp.status_code}")
        except Exception as exc:
            print(f"[GitLabBackend] reset-gitlab 失败: {exc}")


# ==================== 工厂函数 ====================

_backend_instance = None


def get_backend(env_name=None):
    """根据环境名返回后端单例"""
    global _backend_instance
    if env_name is None:
        env_name = os.environ.get("PIPELINE_ENV", "gitlab")

    if _backend_instance is not None:
        return _backend_instance

    if env_name == "gitlab":
        _backend_instance = GitLabBackend()
    else:
        raise ValueError(f"未知环境后端: {env_name}。当前支持: gitlab")

    return _backend_instance


def reset_backend():
    """重置后端单例（测试用）"""
    global _backend_instance
    _backend_instance = None
