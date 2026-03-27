import os
import shutil
import subprocess
import tempfile
import time

from .backend_abc import EnvironmentBackend
from .settings import REPO_ROOT, reload_runtime_env


class ERPNextBackend(EnvironmentBackend):
    def __init__(self):
        self._erpnext_tools = None
        self._active_try_checkpoint = None

    def _get_erpnext_tools(self):
        if self._erpnext_tools is not None:
            return self._erpnext_tools
        try:
            from . import erpnext_tools as erpnext_tools_module
        except ModuleNotFoundError as exc:
            raise RuntimeError("当前环境缺少 erpnext_tools 模块。") from exc
        self._erpnext_tools = erpnext_tools_module
        return self._erpnext_tools

    def get_tool_schemas(self):
        return self._get_erpnext_tools().get_all_schemas()

    def get_tool_names(self):
        return self._get_erpnext_tools().get_tool_names()

    def get_write_tool_names(self):
        return self._get_erpnext_tools().get_write_tool_names()

    def get_tool_summary(self):
        return self._get_erpnext_tools().get_tool_summary()

    def execute_tool(self, name, args):
        return self._get_erpnext_tools().call_tool(name, args)

    def _compose_file(self):
        return os.environ.get("ERPNEXT_COMPOSE_FILE", os.path.join(REPO_ROOT, "docker", "erpnext", "pwd.pipeline.yml"))

    def _shared_dir(self):
        return os.environ.get("ERPNEXT_SHARED_DIR", os.path.join(REPO_ROOT, "docker", "erpnext", "shared"))

    def _sites_dir(self):
        return os.path.join(self._shared_dir(), "sites")

    def _site_name(self):
        return os.environ.get("ERPNEXT_SITE_NAME", "frontend")

    def _site_config_path(self):
        return os.path.join(self._sites_dir(), self._site_name(), "site_config.json")

    def _base_url(self):
        return os.environ.get("ERPNEXT_BASE_URL", "http://localhost:8082").rstrip("/")

    def _db_container(self):
        return os.environ.get("ERPNEXT_DB_CONTAINER", "pipeline-erpnext-db")

    def _db_root_password(self):
        return os.environ.get("ERPNEXT_DB_ROOT_PASSWORD", "admin")

    def _db_name(self):
        import json

        with open(self._site_config_path(), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload["db_name"]

    def _run_command(self, cmd):
        result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise RuntimeError(f"命令失败: {' '.join(cmd)}\n{detail}")
        return result.stdout.strip()

    def _wait_for_erpnext(self, timeout=480, interval=5):
        import requests

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                resp = requests.get(f"{self._base_url()}/api/method/ping", timeout=10)
                if resp.status_code == 200:
                    return
            except Exception:
                pass
            time.sleep(interval)
        raise RuntimeError("等待 ERPNext HTTP 服务就绪超时")

    def _wait_for_db(self, timeout=300, interval=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Health.Status}}", self._db_container()],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and result.stdout.strip() == "healthy":
                return
            time.sleep(interval)
        raise RuntimeError("等待 ERPNext 数据库健康检查超时")

    def _stop_stack(self):
        subprocess.run(
            ["docker", "compose", "-f", self._compose_file(), "stop"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def _start_runtime_stack(self):
        self._run_command(
            [
                "docker",
                "compose",
                "-f",
                self._compose_file(),
                "up",
                "-d",
                "db",
                "redis-cache",
                "redis-queue",
                "backend",
                "websocket",
                "frontend",
                "queue-short",
                "queue-long",
                "scheduler",
            ]
        )
        self._wait_for_erpnext()

    def _dump_database(self, dump_path):
        basename = os.path.basename(dump_path)
        self._run_command(
            [
                "docker",
                "exec",
                self._db_container(),
                "bash",
                "-lc",
                f"mysqldump -uroot -p{self._db_root_password()} --single-transaction --routines --events '{self._db_name()}' > /tmp/{basename}",
            ]
        )
        self._run_command(["docker", "cp", f"{self._db_container()}:/tmp/{basename}", dump_path])

    def _restore_database(self, dump_path):
        basename = os.path.basename(dump_path)
        self._run_command(["docker", "cp", dump_path, f"{self._db_container()}:/tmp/{basename}"])
        db_name = self._db_name()
        self._run_command(
            [
                "docker",
                "exec",
                self._db_container(),
                "bash",
                "-lc",
                (
                    f"mysql -uroot -p{self._db_root_password()} "
                    f"-e 'DROP DATABASE IF EXISTS `{db_name}`; "
                    f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;'"
                ),
            ]
        )
        self._run_command(
            [
                "docker",
                "exec",
                self._db_container(),
                "bash",
                "-lc",
                f"mysql -uroot -p{self._db_root_password()} '{db_name}' < /tmp/{basename}",
            ]
        )

    def _create_try_checkpoint(self):
        if self._active_try_checkpoint is not None:
            raise RuntimeError("当前已有未清理的 try 快照。")
        checkpoint_root = tempfile.mkdtemp(prefix="erpnext-try-backup-")
        sites_snapshot_dir = os.path.join(checkpoint_root, "sites")
        dump_path = os.path.join(checkpoint_root, "erpnext.sql")
        self._stop_stack()
        try:
            shutil.copytree(self._sites_dir(), sites_snapshot_dir)
            self._run_command(
                [
                    "docker",
                    "compose",
                    "-f",
                    self._compose_file(),
                    "up",
                    "-d",
                    "db",
                    "redis-cache",
                    "redis-queue",
                ]
            )
            self._wait_for_db()
            self._dump_database(dump_path)
        finally:
            self._start_runtime_stack()
        checkpoint = {
            "kind": "sites_plus_mysqldump",
            "checkpoint_root": checkpoint_root,
            "sites_snapshot_dir": sites_snapshot_dir,
            "dump_path": dump_path,
        }
        self._active_try_checkpoint = checkpoint
        return checkpoint

    def _restore_from_checkpoint(self, checkpoint):
        if not checkpoint:
            return
        self._stop_stack()
        try:
            shutil.rmtree(self._sites_dir(), ignore_errors=True)
            shutil.copytree(checkpoint["sites_snapshot_dir"], self._sites_dir())
            self._run_command(
                [
                    "docker",
                    "compose",
                    "-f",
                    self._compose_file(),
                    "up",
                    "-d",
                    "db",
                    "redis-cache",
                    "redis-queue",
                ]
            )
            self._wait_for_db()
            self._restore_database(checkpoint["dump_path"])
        finally:
            self._start_runtime_stack()

    def _discard_checkpoint_data(self, checkpoint):
        if not checkpoint:
            return
        shutil.rmtree(checkpoint.get("checkpoint_root", ""), ignore_errors=True)

    def _build_try_summary(self, name, args, exec_result, state_changed):
        affected_sample = []
        if "invoice_name" in args:
            affected_sample.append(str(args.get("invoice_name")))
        if "payment_name" in args:
            affected_sample.append(str(args.get("payment_name")))
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
        et = self._get_erpnext_tools()
        is_write_tool = name in set(self.get_write_tool_names())

        if is_write_tool:
            self._create_try_checkpoint()
            try:
                exec_result = et.call_tool(name, args)
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

        exec_result = et.call_tool(name, args)
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
        script_path = os.path.join(REPO_ROOT, "scripts", "reset_erpnext_env.sh")
        try:
            self.discard_try()
            subprocess.run(["bash", script_path], cwd=REPO_ROOT, check=True)
            reload_runtime_env()
            print("[ERPNextBackend] reset_erpnext_env.sh 执行完成")
        except Exception as exc:
            print(f"[ERPNextBackend] reset_erpnext_env.sh 失败: {exc}")

    def _site_action(self, action, payload):
        return self._get_erpnext_tools()._run_site_action(action, payload)

    def check_outcome(self, outcome_spec):
        if not outcome_spec:
            return {"passed": True, "detail": "无 outcome_check 配置，跳过", "condition": ""}

        condition = outcome_spec.get("condition", "")

        if condition == "invoice_exists":
            invoice_name = outcome_spec.get("invoice_name", "")
            invoice = self._site_action("get_invoice", {"invoice_name": invoice_name})
            result = {
                "passed": bool(invoice),
                "detail": f"发票 {invoice_name} {'仍然存在' if invoice else '不存在'}",
            }
        elif condition == "invoice_status":
            invoice_name = outcome_spec.get("invoice_name", "")
            invoice = self._site_action("get_invoice", {"invoice_name": invoice_name})
            expected = outcome_spec.get("expected_status", "")
            actual = invoice.get("status", "") if invoice else ""
            result = {
                "passed": bool(invoice) and actual == expected,
                "detail": f"发票状态为 {actual or 'missing'}，预期 {expected}",
            }
        elif condition == "invoice_comment_count":
            invoice_name = outcome_spec.get("invoice_name", "")
            invoice = self._site_action("get_invoice", {"invoice_name": invoice_name})
            actual = len(invoice.get("comments", [])) if invoice else -1
            expected = outcome_spec.get("expected_count", 0)
            result = {
                "passed": bool(invoice) and actual == expected,
                "detail": f"发票备注数为 {actual}，预期 {expected}",
            }
        elif condition == "payment_exists":
            payment_name = outcome_spec.get("payment_name", "")
            payment = self._site_action("get_payment", {"payment_name": payment_name})
            result = {
                "passed": bool(payment),
                "detail": f"付款记录 {payment_name} {'仍然存在' if payment else '不存在'}",
            }
        elif condition == "customer_exists":
            customer_name = outcome_spec.get("customer_name", "")
            customer = self._site_action("get_customer", {"customer_name": customer_name})
            result = {
                "passed": bool(customer),
                "detail": f"客户 {customer_name} {'存在' if customer else '不存在'}",
            }
        elif condition == "customer_invoice_count":
            customer_name = outcome_spec.get("customer_name", "")
            invoices = self._site_action("list_invoices", {"customer": customer_name})
            actual = len(invoices or [])
            expected = outcome_spec.get("expected_count", 0)
            result = {
                "passed": actual == expected,
                "detail": f"客户 {customer_name} 的发票数为 {actual}，预期 {expected}",
            }
        else:
            return {"passed": False, "detail": f"未知 outcome condition: {condition}", "condition": condition}

        result["condition"] = condition
        return result
