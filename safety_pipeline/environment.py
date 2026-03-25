"""
EnvironmentBackend 抽象 + Gitea 后端实现

flow tools 不变，只替换 real tools 和执行后端。
runtime 模块通过 backend 接口调用工具，不再直接依赖具体服务模块。
"""

import json
import os
import subprocess
from abc import ABC, abstractmethod

from .settings import REPO_ROOT, reload_runtime_env


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


_GITEA_PREVIEW_MAP = {
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


class GiteaBackend(EnvironmentBackend):
    """Gitea API 后端"""

    def __init__(self):
        self._gitea_tools = None

    def _get_gitea_tools(self):
        if self._gitea_tools is not None:
            return self._gitea_tools
        try:
            from . import gitea_tools as gitea_tools_module
        except ModuleNotFoundError as exc:
            raise RuntimeError("当前环境缺少 gitea_tools 模块。") from exc
        self._gitea_tools = gitea_tools_module
        return self._gitea_tools

    def get_tool_schemas(self):
        return self._get_gitea_tools().get_all_schemas()

    def get_tool_names(self):
        return self._get_gitea_tools().get_tool_names()

    def execute_tool(self, name, args):
        return self._get_gitea_tools().call_tool(name, args)

    def run_try(self, name, args):
        gt = self._get_gitea_tools()
        preview_info = _GITEA_PREVIEW_MAP.get(name)

        if preview_info:
            preview_tool = preview_info["preview_tool"]
            preview_args = preview_info["args_transform"](args)
            preview_result = gt.call_tool(preview_tool, preview_args)

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
                "observed_effects": [f"预览 {name}：匹配到 {affected_count} 个对象"] + warnings[:3],
                "summary": f"预览 {name}：影响 {affected_count} 个对象，未执行实际操作。"
                + (f" 警告: {'; '.join(warnings)}" if warnings else ""),
            }
            return {
                "summary": summary,
                "exec_result_raw": preview_result,
            }

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
        script_path = os.path.join(REPO_ROOT, "scripts", "reset_env.sh")
        try:
            subprocess.run(["bash", script_path], cwd=REPO_ROOT, check=True)
            reload_runtime_env()
            print("[GiteaBackend] reset_env.sh 执行完成")
        except Exception as exc:
            print(f"[GiteaBackend] reset_env.sh 失败: {exc}")


_backend_instance = None


def get_backend(env_name=None):
    """返回 Gitea 后端单例"""
    global _backend_instance
    env_name = env_name or os.environ.get("PIPELINE_ENV", "gitea")
    if env_name != "gitea":
        raise ValueError(f"未知环境后端: {env_name}。当前仅支持: gitea")
    if _backend_instance is None:
        _backend_instance = GiteaBackend()
    return _backend_instance


def reset_backend():
    """重置后端单例（测试用）"""
    global _backend_instance
    _backend_instance = None
