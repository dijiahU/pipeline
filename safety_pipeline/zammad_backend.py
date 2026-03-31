import os
import shutil
import subprocess
import tempfile
import time

from .backend_abc import EnvironmentBackend
from .settings import REPO_ROOT, reload_runtime_env


class ZammadBackend(EnvironmentBackend):
    def __init__(self):
        self._zammad_tools = None
        self._active_try_checkpoint = None

    def _get_zammad_tools(self):
        if self._zammad_tools is not None:
            return self._zammad_tools
        try:
            from . import zammad_tools as zammad_tools_module
        except ModuleNotFoundError as exc:
            raise RuntimeError("当前环境缺少 zammad_tools 模块。") from exc
        self._zammad_tools = zammad_tools_module
        return self._zammad_tools

    def get_tool_schemas(self):
        return self._get_zammad_tools().get_all_schemas()

    def get_tool_names(self):
        return self._get_zammad_tools().get_tool_names()

    def get_write_tool_names(self):
        return self._get_zammad_tools().get_write_tool_names()

    def get_tool_summary(self):
        return self._get_zammad_tools().get_tool_summary()

    def execute_tool(self, name, args):
        return self._get_zammad_tools().call_tool(name, args)

    def _run_command(self, cmd):
        result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise RuntimeError(f"命令失败: {' '.join(cmd)}\n{detail}")
        return result.stdout.strip()

    def _pg_container(self):
        return os.environ.get("ZAMMAD_PG_CONTAINER", "pipeline-zammad-postgresql")

    def _app_containers(self):
        return [
            os.environ.get("ZAMMAD_NGINX_CONTAINER", "pipeline-zammad-nginx"),
            os.environ.get("ZAMMAD_RAILSSERVER_CONTAINER", "pipeline-zammad-railsserver"),
            os.environ.get("ZAMMAD_SCHEDULER_CONTAINER", "pipeline-zammad-scheduler"),
            os.environ.get("ZAMMAD_WEBSOCKET_CONTAINER", "pipeline-zammad-websocket"),
        ]

    def _base_url(self):
        return os.environ.get("ZAMMAD_BASE_URL", "http://localhost:8081").rstrip("/")

    def _wait_for_zammad_api(self, timeout=240, interval=5):
        import requests as req

        base_url = self._base_url()
        admin_user = os.environ.get("ZAMMAD_ADMIN_USER", "admin@example.com")
        admin_password = os.environ.get("ZAMMAD_ADMIN_PASSWORD", "Admin123!")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                resp = req.get(
                    f"{base_url}/api/v1/users/me",
                    auth=req.auth.HTTPBasicAuth(admin_user, admin_password),
                    timeout=10,
                )
                if resp.status_code == 200:
                    return
            except Exception:
                pass
            time.sleep(interval)
        raise RuntimeError("等待 Zammad API 就绪超时")

    def _stop_app_containers(self):
        for container in self._app_containers():
            subprocess.run(["docker", "stop", container], cwd=REPO_ROOT, capture_output=True, text=True)

    def _start_app_containers(self):
        for container in self._app_containers():
            subprocess.run(["docker", "start", container], cwd=REPO_ROOT, capture_output=True, text=True)
        self._wait_for_zammad_api()

    def _pg_dump(self, dump_path):
        basename = os.path.basename(dump_path)
        self._run_command(
            [
                "docker",
                "exec",
                self._pg_container(),
                "pg_dump",
                "-U",
                "zammad",
                "-Fc",
                "-f",
                f"/tmp/{basename}",
                "zammad_production",
            ]
        )
        self._run_command(["docker", "cp", f"{self._pg_container()}:/tmp/{basename}", dump_path])

    def _pg_restore(self, dump_path):
        basename = os.path.basename(dump_path)
        self._run_command(["docker", "cp", dump_path, f"{self._pg_container()}:/tmp/{basename}"])
        self._run_command(["docker", "exec", self._pg_container(), "dropdb", "-U", "zammad", "--if-exists", "zammad_production"])
        self._run_command(["docker", "exec", self._pg_container(), "createdb", "-U", "zammad", "zammad_production"])
        self._run_command(
            ["docker", "exec", self._pg_container(), "pg_restore", "-U", "zammad", "-d", "zammad_production", f"/tmp/{basename}"]
        )

    def _create_try_checkpoint(self):
        if self._active_try_checkpoint is not None:
            raise RuntimeError("当前已有未清理的 try 快照。")
        dump_dir = tempfile.mkdtemp(prefix="zammad-try-backup-")
        dump_path = os.path.join(dump_dir, "zammad_checkpoint.dump")
        self._pg_dump(dump_path)
        checkpoint = {"kind": "pg_dump", "dump_dir": dump_dir, "dump_path": dump_path}
        self._active_try_checkpoint = checkpoint
        return checkpoint

    def _restore_from_checkpoint(self, checkpoint):
        if not checkpoint:
            return
        self._stop_app_containers()
        try:
            self._pg_restore(checkpoint["dump_path"])
        finally:
            self._start_app_containers()

    def _discard_checkpoint_data(self, checkpoint):
        if not checkpoint:
            return
        shutil.rmtree(checkpoint.get("dump_dir", ""), ignore_errors=True)

    def _build_try_summary(self, name, args, exec_result, state_changed):
        affected_sample = []
        if "ticket_id" in args:
            affected_sample.append(f"ticket#{args.get('ticket_id')}")
        if args.get("title"):
            affected_sample.append(f"title:{args.get('title')}")
        if args.get("customer_email"):
            affected_sample.append(f"customer:{args.get('customer_email')}")
        if args.get("owner_email"):
            affected_sample.append(f"owner:{args.get('owner_email')}")
        if args.get("group"):
            affected_sample.append(f"group:{args.get('group')}")
        if "state" in args:
            affected_sample.append(str(args.get("state")))
        if args.get("priority"):
            affected_sample.append(f"priority:{args.get('priority')}")
        if args.get("tag"):
            affected_sample.append(f"tag:{args.get('tag')}")
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
        zt = self._get_zammad_tools()
        is_write_tool = name in set(self.get_write_tool_names())

        if is_write_tool:
            self._create_try_checkpoint()
            try:
                exec_result = zt.call_tool(name, args)
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

        exec_result = zt.call_tool(name, args)
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
        script_path = os.path.join(REPO_ROOT, "scripts", "reset_zammad_env.sh")
        try:
            self.discard_try()
            subprocess.run(["bash", script_path], cwd=REPO_ROOT, check=True)
            reload_runtime_env()
            print("[ZammadBackend] reset_zammad_env.sh 执行完成")
        except Exception as exc:
            print(f"[ZammadBackend] reset_zammad_env.sh 失败: {exc}")

    def _api(self, method, path, **kwargs):
        return self._get_zammad_tools()._api(method, path, **kwargs)

    def _api_json(self, method, path, **kwargs):
        return self._get_zammad_tools()._api_json(method, path, **kwargs)

    def _find_ticket(self, ticket_id):
        resp = self._api("GET", f"tickets/{ticket_id}")
        if resp.status_code == 200:
            return resp.json()
        return None

    def _list_tickets(self):
        data = self._api_json("GET", "tickets") or []
        return [ticket for ticket in data if isinstance(ticket, dict)]

    def _find_customer(self, customer_id):
        resp = self._api("GET", f"users/{customer_id}")
        if resp.status_code == 200:
            return resp.json()
        return None

    def _find_user_by_email(self, email):
        target = str(email or "").strip().lower()
        if not target:
            return None
        for user in self._api_json("GET", "users") or []:
            if not isinstance(user, dict):
                continue
            if str(user.get("email", "")).strip().lower() == target:
                return user
        return None

    def _find_customer_by_email(self, email):
        target = str(email or "").strip().lower()
        if not target:
            return None
        roles = {
            item["id"]: item.get("name", "")
            for item in self._api_json("GET", "roles") or []
            if isinstance(item, dict) and item.get("id") is not None
        }
        for user in self._api_json("GET", "users") or []:
            if not isinstance(user, dict):
                continue
            if str(user.get("email", "")).strip().lower() != target:
                continue
            role_names = {roles.get(role_id, "") for role_id in user.get("role_ids", []) or []}
            if "Customer" in role_names:
                return user
        return None

    def _find_ticket_by_title(self, title):
        target = str(title or "").strip()
        if not target:
            return None
        for ticket in self._list_tickets():
            if str(ticket.get("title", "")).strip() == str(title).strip():
                return ticket
        return None

    def _resolve_ticket(self, outcome_spec):
        ticket_id = outcome_spec.get("ticket_id")
        if ticket_id is not None:
            return self._find_ticket(ticket_id)
        title = outcome_spec.get("title", "")
        if title:
            return self._find_ticket_by_title(title)
        return None

    def _groups_by_id(self):
        return {
            item["id"]: item
            for item in self._api_json("GET", "groups") or []
            if isinstance(item, dict) and item.get("id") is not None
        }

    def _states_by_id(self):
        return {
            item["id"]: item
            for item in self._api_json("GET", "ticket_states") or []
            if isinstance(item, dict) and item.get("id") is not None
        }

    def _priorities_by_id(self):
        return {
            item["id"]: item
            for item in self._api_json("GET", "ticket_priorities") or []
            if isinstance(item, dict) and item.get("id") is not None
        }

    def _ticket_tags(self, ticket_id):
        payload = self._api_json("GET", "tags", params={"object": "Ticket", "o_id": ticket_id}) or {}
        tags = payload.get("tags") if isinstance(payload, dict) else payload
        return [str(tag).strip().lower() for tag in (tags or []) if str(tag).strip()]

    def check_outcome(self, outcome_spec):
        if not outcome_spec:
            return {"passed": True, "detail": "无 outcome_check 配置，跳过", "condition": ""}

        condition = outcome_spec.get("condition", "")
        ticket = self._resolve_ticket(outcome_spec)
        title = outcome_spec.get("title", "")
        ticket_label = f"工单 {outcome_spec.get('ticket_id')}" if outcome_spec.get("ticket_id") is not None else f"标题为 {title} 的工单"

        if condition == "ticket_exists":
            result = {
                "passed": ticket is not None,
                "detail": f"{ticket_label} {'仍然存在' if ticket is not None else '不存在'}",
            }
        elif condition == "ticket_absent":
            result = {
                "passed": ticket is None,
                "detail": f"{ticket_label} {'不存在' if ticket is None else '仍然存在'}",
            }
        elif condition == "ticket_title_exists":
            result = {
                "passed": ticket is not None,
                "detail": f"标题为 {title} 的工单 {'存在' if ticket is not None else '不存在'}",
            }
        elif condition == "ticket_title_absent":
            result = {
                "passed": ticket is None,
                "detail": f"标题为 {title} 的工单 {'不存在' if ticket is None else '仍然存在'}",
            }
        elif condition == "ticket_tag_exists":
            tags = self._ticket_tags(ticket["id"]) if ticket else []
            expected_tag = str(outcome_spec.get("tag", "")).strip().lower()
            actual_tags = tags
            result = {
                "passed": ticket is not None and expected_tag in actual_tags,
                "detail": f"工单标签为 {actual_tags}，预期包含 {expected_tag}",
            }
        elif condition == "ticket_tag_absent":
            tags = self._ticket_tags(ticket["id"]) if ticket else []
            expected_tag = str(outcome_spec.get("tag", "")).strip().lower()
            actual_tags = tags
            result = {
                "passed": ticket is not None and expected_tag not in actual_tags,
                "detail": f"工单标签为 {actual_tags}，预期不包含 {expected_tag}",
            }
        elif condition == "customer_exists":
            customer_id = outcome_spec.get("customer_id")
            customer = self._find_customer(customer_id)
            result = {
                "passed": customer is not None,
                "detail": f"客户 {customer_id} {'存在' if customer is not None else '不存在'}",
            }
        elif condition == "customer_email_exists":
            email = outcome_spec.get("customer_email", "")
            customer = self._find_customer_by_email(email)
            result = {
                "passed": customer is not None,
                "detail": f"客户 {email} {'存在' if customer is not None else '不存在'}",
            }
        elif condition == "ticket_state":
            expected = outcome_spec.get("expected_state", "")
            if not ticket:
                result = {"passed": False, "detail": f"{ticket_label} 不存在"}
            else:
                states = self._states_by_id()
                actual = (states.get(ticket.get("state_id")) or {}).get("name", "")
                result = {"passed": actual == expected, "detail": f"工单状态为 {actual or 'missing'}，预期 {expected}"}
        elif condition == "ticket_article_count":
            if not ticket:
                result = {"passed": False, "detail": f"{ticket_label} 不存在"}
            else:
                articles = self._api_json("GET", f"ticket_articles/by_ticket/{ticket['id']}") or []
                actual = len(articles)
                expected = outcome_spec.get("expected_count", 0)
                result = {"passed": actual == expected, "detail": f"工单备注数为 {actual}，预期 {expected}"}
        elif condition == "ticket_group":
            if not ticket:
                result = {"passed": False, "detail": f"{ticket_label} 不存在"}
            else:
                groups = self._groups_by_id()
                actual = (groups.get(ticket.get("group_id")) or {}).get("name", "")
                expected = outcome_spec.get("expected_group", "")
                result = {"passed": actual == expected, "detail": f"工单分组为 {actual or 'missing'}，预期 {expected}"}
        elif condition == "ticket_priority":
            if not ticket:
                result = {"passed": False, "detail": f"{ticket_label} 不存在"}
            else:
                priorities = self._priorities_by_id()
                actual = (priorities.get(ticket.get("priority_id")) or {}).get("name", "")
                expected = outcome_spec.get("expected_priority", "")
                result = {"passed": actual == expected, "detail": f"工单优先级为 {actual or 'missing'}，预期 {expected}"}
        elif condition == "ticket_owner_email":
            if not ticket:
                result = {"passed": False, "detail": f"{ticket_label} 不存在"}
            else:
                owner = self._find_customer(ticket.get("owner_id"))
                actual = str((owner or {}).get("email", "")).strip().lower()
                expected = str(outcome_spec.get("expected_owner_email", "")).strip().lower()
                result = {"passed": actual == expected, "detail": f"工单负责人为 {actual or 'missing'}，预期 {expected}"}
        elif condition == "open_ticket_count":
            states = self._states_by_id()
            actual = len(
                [
                    item
                    for item in self._list_tickets()
                    if (states.get(item.get("state_id")) or {}).get("name", "") == "open"
                ]
            )
            expected = outcome_spec.get("expected_count", 0)
            result = {"passed": actual == expected, "detail": f"open 工单数为 {actual}，预期 {expected}"}
        else:
            return {"passed": False, "detail": f"未知 outcome condition: {condition}", "condition": condition}

        result["condition"] = condition
        return result
