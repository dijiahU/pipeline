"""EnvironmentBackend 抽象基类。

独立模块，避免 environment.py 与各 *_backend.py 之间的循环导入。
"""

from abc import ABC, abstractmethod


class EnvironmentBackend(ABC):
    """所有环境后端的公共接口"""

    @abstractmethod
    def get_tool_schemas(self):
        """返回 OpenAI function calling 格式的 tool schema 列表"""

    @abstractmethod
    def get_tool_names(self):
        """返回所有已注册的 tool 名称列表"""

    @abstractmethod
    def get_write_tool_names(self):
        """返回所有写工具名称列表"""

    @abstractmethod
    def get_tool_summary(self):
        """返回当前后端工具摘要"""

    @abstractmethod
    def execute_tool(self, name, args):
        """真实执行工具，返回结果字符串"""

    @abstractmethod
    def run_try(self, name, args):
        """try 执行，返回 {"summary": dict, "exec_result_raw": str}"""

    @abstractmethod
    def commit_try(self):
        """提交 try 执行结果并清理临时快照"""

    @abstractmethod
    def rollback_try(self):
        """回滚到 try 之前的环境状态并清理临时快照"""

    @abstractmethod
    def discard_try(self):
        """丢弃 try 快照但不回滚当前环境"""

    @abstractmethod
    def reset(self):
        """episode 间重置环境"""

    @abstractmethod
    def check_outcome(self, outcome_spec):
        """根据 outcome_check 配置校验最终状态"""
