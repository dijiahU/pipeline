import importlib
import os
import shutil
import subprocess
import tempfile

from .local_state_store import default_seed_manifest_path, load_json_state
from .settings import REPO_ROOT, reload_runtime_env


class LocalStateServiceBackend:
    def __init__(self, service_id):
        self.service_id = service_id
        self._tools_module = None
        self._active_try_checkpoint = None

    def _get_tools_module(self):
        if self._tools_module is not None:
            return self._tools_module
        self._tools_module = importlib.import_module(f".{self.service_id}_tools", package="safety_pipeline")
        return self._tools_module

    def _state_path(self):
        return self._get_tools_module()._state_path()

    def _load_state(self):
        return load_json_state(self._state_path(), seed_path=default_seed_manifest_path(self.service_id))

    def get_tool_schemas(self):
        return self._get_tools_module().get_all_schemas()

    def get_tool_names(self):
        return self._get_tools_module().get_tool_names()

    def get_write_tool_names(self):
        return self._get_tools_module().get_write_tool_names()

    def get_tool_summary(self):
        return self._get_tools_module().get_tool_summary()

    def execute_tool(self, name, args):
        return self._get_tools_module().call_tool(name, args)

    def _build_try_summary(self, name, args, exec_result, state_changed):
        affected = []
        for key in (
            "ticket_id",
            "invoice_name",
            "payment_name",
            "patient_id",
            "appointment_id",
            "topic_id",
            "channel_name",
            "path",
            "name",
        ):
            value = args.get(key)
            if value not in (None, "", []):
                affected.append(str(value))
        if not affected and args:
            first_key = sorted(args.keys())[0]
            affected.append(f"{first_key}={args[first_key]}")

        return {
            "exec_status": "success",
            "state_changed": state_changed,
            "affected_objects_count": len(affected[:3]),
            "affected_objects_sample": affected[:3],
            "unexpected_side_effect": False,
            "observed_effects": [f"tool_try 已真实执行 {name}"] if state_changed else [f"只读工具 {name} 执行成功"],
            "summary": (
                f"tool_try 已真实执行 {name}，当前结果可在 unsafe->ask_human 时回滚。"
                if state_changed
                else f"{name} 执行完成，无副作用。"
            ),
            "exec_result": exec_result,
        }

    def _create_try_checkpoint(self):
        if self._active_try_checkpoint is not None:
            raise RuntimeError("当前已有未清理的 try 快照。")
        state_path = self._state_path()
        load_json_state(state_path, seed_path=default_seed_manifest_path(self.service_id))
        checkpoint_dir = tempfile.mkdtemp(prefix=f"{self.service_id}-try-backup-")
        checkpoint_path = os.path.join(checkpoint_dir, os.path.basename(state_path))
        shutil.copy2(state_path, checkpoint_path)
        self._active_try_checkpoint = {
            "checkpoint_dir": checkpoint_dir,
            "checkpoint_path": checkpoint_path,
            "state_path": state_path,
        }
        return self._active_try_checkpoint

    def _restore_from_checkpoint(self, checkpoint):
        shutil.copy2(checkpoint["checkpoint_path"], checkpoint["state_path"])

    def _discard_checkpoint_data(self, checkpoint):
        if not checkpoint:
            return
        shutil.rmtree(checkpoint.get("checkpoint_dir", ""), ignore_errors=True)

    def run_try(self, name, args):
        is_write_tool = name in set(self.get_write_tool_names())
        tools_module = self._get_tools_module()

        if is_write_tool:
            self._create_try_checkpoint()
            try:
                exec_result = tools_module.call_tool(name, args)
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

        exec_result = tools_module.call_tool(name, args)
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
        script_path = os.path.join(REPO_ROOT, "scripts", f"reset_{self.service_id}_env.sh")
        try:
            self.discard_try()
            subprocess.run(["bash", script_path], cwd=REPO_ROOT, check=True)
            reload_runtime_env()
            print(f"[{self.service_id}] {os.path.basename(script_path)} 执行完成")
        except Exception as exc:
            print(f"[{self.service_id}] {os.path.basename(script_path)} 失败: {exc}")
