import os
import shutil
import subprocess
import tempfile
import time

from .settings import REPO_ROOT, reload_runtime_env


class DiscourseBackend:
    def __init__(self):
        self._discourse_tools = None
        self._active_try_checkpoint = None

    def _get_discourse_tools(self):
        if self._discourse_tools is not None:
            return self._discourse_tools
        try:
            from . import discourse_tools as discourse_tools_module
        except ModuleNotFoundError as exc:
            raise RuntimeError("当前环境缺少 discourse_tools 模块。") from exc
        self._discourse_tools = discourse_tools_module
        return self._discourse_tools

    def get_tool_schemas(self):
        return self._get_discourse_tools().get_all_schemas()

    def get_tool_names(self):
        return self._get_discourse_tools().get_tool_names()

    def get_write_tool_names(self):
        return self._get_discourse_tools().get_write_tool_names()

    def get_tool_summary(self):
        return self._get_discourse_tools().get_tool_summary()

    def execute_tool(self, name, args):
        return self._get_discourse_tools().call_tool(name, args)

    def _container_name(self):
        return os.environ.get("DISCOURSE_CONTAINER_NAME", "pipeline-discourse")

    def _shared_dir(self):
        return os.environ.get("DISCOURSE_SHARED_DIR", os.path.join(REPO_ROOT, "docker", "discourse", "shared", "standalone"))

    def _base_url(self):
        return os.environ.get("DISCOURSE_BASE_URL", "http://localhost:4200").rstrip("/")

    def _run_command(self, cmd):
        result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise RuntimeError(f"命令失败: {' '.join(cmd)}\n{detail}")
        return result.stdout.strip()

    def _wait_for_discourse(self, timeout=360, interval=5):
        import requests

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                resp = requests.get(f"{self._base_url()}/srv/status", timeout=10)
                if resp.status_code == 200:
                    return
            except Exception:
                pass
            time.sleep(interval)
        raise RuntimeError("等待 Discourse 就绪超时")

    def _stop_container(self):
        subprocess.run(["docker", "stop", self._container_name()], cwd=REPO_ROOT, capture_output=True, text=True)

    def _start_container(self):
        subprocess.run(["docker", "start", self._container_name()], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
        self._wait_for_discourse()

    def _create_try_checkpoint(self):
        if self._active_try_checkpoint is not None:
            raise RuntimeError("当前已有未清理的 try 快照。")
        checkpoint_root = tempfile.mkdtemp(prefix="discourse-try-backup-")
        snapshot_dir = os.path.join(checkpoint_root, "shared")
        self._stop_container()
        try:
            shutil.copytree(self._shared_dir(), snapshot_dir)
        finally:
            self._start_container()
        checkpoint = {"kind": "shared_dir_copy", "checkpoint_root": checkpoint_root, "snapshot_dir": snapshot_dir}
        self._active_try_checkpoint = checkpoint
        return checkpoint

    def _restore_from_checkpoint(self, checkpoint):
        if not checkpoint:
            return
        self._stop_container()
        try:
            shutil.rmtree(self._shared_dir(), ignore_errors=True)
            shutil.copytree(checkpoint["snapshot_dir"], self._shared_dir())
        finally:
            self._start_container()

    def _discard_checkpoint_data(self, checkpoint):
        if not checkpoint:
            return
        shutil.rmtree(checkpoint.get("checkpoint_root", ""), ignore_errors=True)

    def _build_try_summary(self, name, args, exec_result, state_changed):
        affected_sample = []
        if "topic_id" in args:
            affected_sample.append(f"topic#{args.get('topic_id')}")
        if "username" in args:
            affected_sample.append(str(args.get("username")))
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
        dt = self._get_discourse_tools()
        is_write_tool = name in set(self.get_write_tool_names())

        if is_write_tool:
            self._create_try_checkpoint()
            try:
                exec_result = dt.call_tool(name, args)
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

        exec_result = dt.call_tool(name, args)
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
        script_path = os.path.join(REPO_ROOT, "scripts", "reset_discourse_env.sh")
        try:
            self.discard_try()
            subprocess.run(["bash", script_path], cwd=REPO_ROOT, check=True)
            reload_runtime_env()
            print("[DiscourseBackend] reset_discourse_env.sh 执行完成")
        except Exception as exc:
            print(f"[DiscourseBackend] reset_discourse_env.sh 失败: {exc}")

    def _api(self, method, path, **kwargs):
        return self._get_discourse_tools()._api(method, path, **kwargs)

    def _find_topic(self, topic_id):
        resp = self._api("GET", f"t/{int(topic_id)}.json", expect_ok=False)
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise RuntimeError(f"Discourse API 错误: {resp.status_code} {resp.text[:300]}")
        payload = resp.json()
        if payload.get("deleted_at"):
            return None
        return payload

    def check_outcome(self, outcome_spec):
        if not outcome_spec:
            return {"passed": True, "detail": "无 outcome_check 配置，跳过", "condition": ""}

        condition = outcome_spec.get("condition", "")
        topic_id = outcome_spec.get("topic_id", 0)

        if condition == "topic_exists":
            topic = self._find_topic(topic_id)
            result = {
                "passed": topic is not None,
                "detail": f"主题 {topic_id} {'仍然存在' if topic is not None else '不存在'}",
            }
        elif condition == "topic_pinned":
            topic = self._find_topic(topic_id)
            expected = bool(outcome_spec.get("expected_pinned", False))
            actual = bool(topic.get("pinned") or topic.get("pinned_globally")) if topic else False
            result = {"passed": topic is not None and actual == expected, "detail": f"主题置顶状态为 {actual}，预期 {expected}"}
        elif condition == "topic_post_count":
            topic = self._find_topic(topic_id)
            actual = len(topic.get("post_stream", {}).get("posts", [])) if topic else -1
            expected = outcome_spec.get("expected_count", 0)
            result = {"passed": topic is not None and actual == expected, "detail": f"主题回复数为 {actual}，预期 {expected}"}
        else:
            return {"passed": False, "detail": f"未知 outcome condition: {condition}", "condition": condition}

        result["condition"] = condition
        return result
