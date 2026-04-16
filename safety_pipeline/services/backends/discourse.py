import os
import subprocess

from ...backend_abc import EnvironmentBackend
from ...settings import REPO_ROOT, reload_runtime_env


class DiscourseBackend(EnvironmentBackend):
    def __init__(self):
        self._discourse_tools = None

    def _get_discourse_tools(self):
        if self._discourse_tools is not None:
            return self._discourse_tools
        try:
            from ..tools import discourse as discourse_tools_module
        except ModuleNotFoundError as exc:
            raise RuntimeError("The current environment is missing the discourse_tools module.") from exc
        self._discourse_tools = discourse_tools_module
        return self._discourse_tools

    def get_tool_schemas(self):
        return self._get_discourse_tools().get_all_schemas()

    def get_tool_names(self):
        return self._get_discourse_tools().get_tool_names()

    def execute_tool(self, name, args):
        return self._get_discourse_tools().call_tool(name, args)

    def reset(self):
        script_path = os.path.join(REPO_ROOT, "scripts", "reset_discourse_env.sh")
        try:
            subprocess.run(["bash", script_path], cwd=REPO_ROOT, check=True)
            reload_runtime_env()
            print("[DiscourseBackend] reset_discourse_env.sh completed")
        except Exception as exc:
            print(f"[DiscourseBackend] reset_discourse_env.sh failed: {exc}")
