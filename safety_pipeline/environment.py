"""
EnvironmentBackend 抽象 + Gitea 后端实现

flow tools 不变，只替换 real tools 和执行后端。
runtime 模块通过 backend 接口调用工具，不再直接依赖具体服务模块。
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
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

class GiteaBackend(EnvironmentBackend):
    """Gitea API 后端"""

    def __init__(self):
        self._gitea_tools = None
        self._active_try_checkpoint = None

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

    def _run_command(self, cmd):
        result = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise RuntimeError(f"命令失败: {' '.join(cmd)}\n{detail}")
        return result.stdout.strip()

    def _wait_for_gitea_api(self, timeout=120, interval=2):
        base_url = os.environ.get("GITEA_BASE_URL", "http://localhost:3000").rstrip("/")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                gt = self._get_gitea_tools()
                resp = gt._api("GET", "version")
                if resp.status_code == 200:
                    return
            except Exception:
                pass
            time.sleep(interval)
        raise RuntimeError("等待 Gitea API 就绪超时")

    def _container_name(self):
        return os.environ.get("SANDBOX_CONTAINER_NAME") or os.environ.get("GITEA_CONTAINER_NAME") or "pipeline-gitea"

    def _helper_image(self):
        container_name = self._container_name()
        image = self._run_command(["docker", "inspect", container_name, "--format", "{{.Config.Image}}"])
        return image or "gitea/gitea:1.22"

    def _inspect_data_mount(self):
        container_name = self._container_name()
        raw = self._run_command(["docker", "inspect", container_name, "--format", "{{json .Mounts}}"])
        mounts = json.loads(raw or "[]")
        for mount in mounts:
            if mount.get("Destination") == "/data":
                return mount
        raise RuntimeError("未找到 Gitea 容器 /data 挂载点，无法为 tool_try 创建快照。")

    def _stop_container(self):
        self._run_command(["docker", "stop", self._container_name()])

    def _start_container(self):
        self._run_command(["docker", "start", self._container_name()])
        self._wait_for_gitea_api()

    def _copy_volume_to_volume(self, source_name, target_name, image):
        self._run_command(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{source_name}:/from:ro",
                "-v",
                f"{target_name}:/to",
                image,
                "sh",
                "-c",
                "mkdir -p /to && cd /from && tar -cf - . | tar -xf - -C /to",
            ]
        )

    def _clear_volume(self, volume_name, image):
        self._run_command(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{volume_name}:/to",
                image,
                "sh",
                "-c",
                "find /to -mindepth 1 -maxdepth 1 -exec rm -rf {} +",
            ]
        )

    def _create_bind_backup(self, source_path):
        backup_dir = tempfile.mkdtemp(prefix="gitea-try-backup-")
        backup_path = os.path.join(backup_dir, "data")
        shutil.copytree(source_path, backup_path, dirs_exist_ok=True)
        return {"kind": "bind", "source_path": source_path, "backup_dir": backup_dir, "backup_path": backup_path}

    def _restore_bind_backup(self, checkpoint):
        source_path = checkpoint["source_path"]
        backup_path = checkpoint["backup_path"]
        if os.path.isdir(source_path):
            for entry in os.listdir(source_path):
                target = os.path.join(source_path, entry)
                if os.path.isdir(target) and not os.path.islink(target):
                    shutil.rmtree(target)
                else:
                    os.unlink(target)
        else:
            os.makedirs(source_path, exist_ok=True)
        for entry in os.listdir(backup_path):
            src = os.path.join(backup_path, entry)
            dst = os.path.join(source_path, entry)
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)

    def _discard_checkpoint_data(self, checkpoint):
        if not checkpoint:
            return
        if checkpoint.get("kind") == "volume":
            self._run_command(["docker", "volume", "rm", "-f", checkpoint["backup_name"]])
        elif checkpoint.get("kind") == "bind":
            shutil.rmtree(checkpoint["backup_dir"], ignore_errors=True)

    def _create_try_checkpoint(self):
        if self._active_try_checkpoint is not None:
            raise RuntimeError("当前已有未清理的 try 快照。")

        mount = self._inspect_data_mount()
        image = self._helper_image()
        checkpoint = None
        self._stop_container()
        try:
            if mount.get("Type") == "volume":
                source_name = mount.get("Name")
                if not source_name:
                    raise RuntimeError("未找到 Gitea 数据 volume 名称。")
                backup_name = f"{self._container_name()}-try-{uuid.uuid4().hex[:12]}"
                self._run_command(["docker", "volume", "create", backup_name])
                self._copy_volume_to_volume(source_name, backup_name, image)
                checkpoint = {
                    "kind": "volume",
                    "source_name": source_name,
                    "backup_name": backup_name,
                    "image": image,
                }
            elif mount.get("Type") == "bind":
                checkpoint = self._create_bind_backup(mount.get("Source", ""))
            else:
                raise RuntimeError(f"暂不支持的 Gitea 数据挂载类型: {mount.get('Type')}")
        except Exception:
            self._discard_checkpoint_data(checkpoint)
            self._start_container()
            raise
        self._start_container()
        self._active_try_checkpoint = checkpoint
        return checkpoint

    def _restore_from_checkpoint(self, checkpoint):
        if not checkpoint:
            return
        self._stop_container()
        try:
            if checkpoint.get("kind") == "volume":
                image = checkpoint["image"]
                self._clear_volume(checkpoint["source_name"], image)
                self._copy_volume_to_volume(checkpoint["backup_name"], checkpoint["source_name"], image)
            elif checkpoint.get("kind") == "bind":
                self._restore_bind_backup(checkpoint)
        finally:
            self._start_container()

    def _build_try_summary(self, name, args, exec_result, state_changed):
        affected_sample = []
        if name == "delete_branch":
            affected_sample = [args.get("branch_name", "")]
        elif name == "close_issue":
            affected_sample = [f"issue#{args.get('issue_iid', '')}"]
        elif name == "update_branch_protection":
            affected_sample = [args.get("branch_name", "")]

        return {
            "exec_status": "success",
            "state_changed": state_changed,
            "affected_objects_count": len([item for item in affected_sample if item]),
            "affected_objects_sample": [item for item in affected_sample if item],
            "unexpected_side_effect": False,
            "observed_effects": [f"tool_try 已真实执行 {name}"] if state_changed else [f"只读工具 {name} 执行成功"],
            "summary": (
                f"tool_try 已真实执行 {name}，当前结果可在 unsafe->ask_human 时回滚。"
                if state_changed
                else f"{name} 执行完成，无副作用。"
            ),
            "exec_result": exec_result,
        }

    def run_try(self, name, args):
        gt = self._get_gitea_tools()
        is_write_tool = name in {"delete_branch", "close_issue", "update_branch_protection"}

        if is_write_tool:
            self._create_try_checkpoint()
            try:
                exec_result = gt.call_tool(name, args)
            except Exception:
                try:
                    self.rollback_try()
                except Exception:
                    self._active_try_checkpoint = None
                raise
            return {
                "summary": self._build_try_summary(name, args, exec_result, state_changed=True),
                "exec_result_raw": exec_result,
            }

        exec_result = gt.call_tool(name, args)
        return {
            "summary": self._build_try_summary(name, args, exec_result, state_changed=False),
            "exec_result_raw": exec_result,
        }

    def commit_try(self):
        if self._active_try_checkpoint is None:
            return False
        checkpoint = self._active_try_checkpoint
        self._active_try_checkpoint = None
        self._discard_checkpoint_data(checkpoint)
        return True

    def rollback_try(self):
        if self._active_try_checkpoint is None:
            return False
        checkpoint = self._active_try_checkpoint
        try:
            self._restore_from_checkpoint(checkpoint)
        finally:
            self._active_try_checkpoint = None
            self._discard_checkpoint_data(checkpoint)
        return True

    def discard_try(self):
        if self._active_try_checkpoint is None:
            return False
        checkpoint = self._active_try_checkpoint
        self._active_try_checkpoint = None
        self._discard_checkpoint_data(checkpoint)
        return True

    def reset(self):
        script_path = os.path.join(REPO_ROOT, "scripts", "reset_env.sh")
        try:
            self.discard_try()
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
