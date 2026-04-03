"""
Mailu 邮件服务后端。

Admin 数据通过 docker cp 备份 SQLite DB，
邮件数据通过 docker exec tar 备份 Maildir。
邮件发送操作标记为不可逆。
"""

import json
import os
import shutil
import subprocess
import tempfile
import time

from .backend_abc import EnvironmentBackend
from .settings import REPO_ROOT, reload_runtime_env


class MailuBackend(EnvironmentBackend):
    def __init__(self):
        self._mailu_tools = None
        self._active_try_checkpoint = None

    def _get_mailu_tools(self):
        if self._mailu_tools is not None:
            return self._mailu_tools
        try:
            from . import mailu_tools as mailu_tools_module
        except ModuleNotFoundError as exc:
            raise RuntimeError("当前环境缺少 mailu_tools 模块。") from exc
        self._mailu_tools = mailu_tools_module
        return self._mailu_tools

    def get_tool_schemas(self):
        return self._get_mailu_tools().get_all_schemas()

    def get_tool_names(self):
        return self._get_mailu_tools().get_tool_names()

    def get_write_tool_names(self):
        return self._get_mailu_tools().get_write_tool_names()

    def get_tool_summary(self):
        return self._get_mailu_tools().get_tool_summary()

    def execute_tool(self, name, args):
        return self._get_mailu_tools().call_tool(name, args)

    def _admin_container(self):
        return os.environ.get("MAILU_ADMIN_CONTAINER", "pipeline-mailu-admin")

    def _dovecot_container(self):
        return os.environ.get("MAILU_DOVECOT_CONTAINER", "pipeline-mailu-dovecot")

    def _base_url(self):
        return os.environ.get("MAILU_BASE_URL", "http://localhost:8443").rstrip("/")

    def _api_token(self):
        return os.environ.get("MAILU_API_TOKEN", "")

    _IRREVERSIBLE_TOOLS = {"send_email", "send_email_with_attachment"}

    def _run_cmd(self, cmd, check=True):
        result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise RuntimeError(f"命令失败: {' '.join(cmd)}\n{detail}")
        return result.stdout.strip()

    def _create_try_checkpoint(self):
        if self._active_try_checkpoint is not None:
            raise RuntimeError("当前已有未清理的 try 快照。")
        checkpoint_root = tempfile.mkdtemp(prefix="mailu-try-backup-")
        db_path = os.path.join(checkpoint_root, "main.db")
        mail_tar = os.path.join(checkpoint_root, "mail.tar")
        self._run_cmd([
            "docker", "cp",
            f"{self._admin_container()}:/data/main.db",
            db_path,
        ])
        self._run_cmd([
            "docker", "exec", self._dovecot_container(),
            "tar", "cf", "-", "/mail",
        ])
        tar_result = subprocess.run(
            ["docker", "exec", self._dovecot_container(), "tar", "cf", "-", "/mail"],
            cwd=REPO_ROOT, capture_output=True,
        )
        if tar_result.returncode == 0:
            with open(mail_tar, "wb") as f:
                f.write(tar_result.stdout)
        checkpoint = {
            "kind": "mailu_db_mail",
            "checkpoint_root": checkpoint_root,
            "db_path": db_path,
            "mail_tar": mail_tar,
        }
        self._active_try_checkpoint = checkpoint
        return checkpoint

    def _restore_from_checkpoint(self, checkpoint):
        if not checkpoint:
            return
        subprocess.run(
            ["docker", "stop", self._admin_container(), self._dovecot_container()],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        try:
            self._run_cmd([
                "docker", "cp",
                checkpoint["db_path"],
                f"{self._admin_container()}:/data/main.db",
            ], check=False)
            if os.path.exists(checkpoint.get("mail_tar", "")):
                subprocess.run(
                    ["docker", "cp", "-", f"{self._dovecot_container()}:/"],
                    input=open(checkpoint["mail_tar"], "rb").read(),
                    cwd=REPO_ROOT, capture_output=True,
                )
        finally:
            subprocess.run(
                ["docker", "start", self._admin_container(), self._dovecot_container()],
                cwd=REPO_ROOT, capture_output=True, text=True,
            )
            time.sleep(5)

    def _discard_checkpoint_data(self, checkpoint):
        if not checkpoint:
            return
        shutil.rmtree(checkpoint.get("checkpoint_root", ""), ignore_errors=True)

    def _build_try_summary(self, name, args, exec_result, state_changed):
        affected_sample = []
        irreversible = name in self._IRREVERSIBLE_TOOLS

        if "domain" in args:
            affected_sample.append(f"domain:{args.get('domain')}")
        if "email" in args:
            affected_sample.append(f"user:{args.get('email')}")
        if "alias_email" in args:
            affected_sample.append(f"alias:{args.get('alias_email')}")
        if "from_email" in args:
            affected_sample.append(f"from:{args.get('from_email')}")
        if "to_email" in args:
            affected_sample.append(f"to:{args.get('to_email')}")
        if "relay_name" in args:
            affected_sample.append(f"relay:{args.get('relay_name')}")
        if "alternative_name" in args:
            affected_sample.append(f"alt_domain:{args.get('alternative_name')}")
        if "destination" in args:
            affected_sample.append(f"destination:{args.get('destination')}")
        if "subject" in args:
            affected_sample.append(f"subject:{args.get('subject')}")

        if irreversible and state_changed:
            summary_text = f"tool_try 已真实执行 {name}，此操作不可撤回（邮件已发送）。"
        elif state_changed:
            summary_text = f"tool_try 已真实执行 {name}，当前结果可在 unsafe->ask_human 时回滚。"
        else:
            summary_text = f"{name} 执行完成，无副作用。"

        return {
            "exec_status": "success",
            "state_changed": state_changed,
            "irreversible": irreversible,
            "affected_objects_count": len([i for i in affected_sample if i]),
            "affected_objects_sample": [i for i in affected_sample if i],
            "unexpected_side_effect": False,
            "observed_effects": [f"tool_try 已真实执行 {name}"] if state_changed else [f"只读工具 {name} 执行成功"],
            "summary": summary_text,
            "exec_result": exec_result,
        }

    def run_try(self, name, args):
        mt = self._get_mailu_tools()
        is_write_tool = name in set(self.get_write_tool_names())

        if is_write_tool:
            self._create_try_checkpoint()
            try:
                exec_result = mt.call_tool(name, args)
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

        exec_result = mt.call_tool(name, args)
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
        script_path = os.path.join(REPO_ROOT, "scripts", "reset_mailu_env.sh")
        try:
            self.discard_try()
            subprocess.run(["bash", script_path], cwd=REPO_ROOT, check=True)
            reload_runtime_env()
            print("[MailuBackend] reset_mailu_env.sh 执行完成")
        except Exception as exc:
            print(f"[MailuBackend] reset_mailu_env.sh 失败: {exc}")

    # -----------------------------------------------------------------------
    # outcome checks
    # -----------------------------------------------------------------------

    def _admin_api(self, method, path):
        import requests
        url = f"{self._base_url()}/api/v1/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {self._api_token()}"}
        resp = requests.request(method, url, headers=headers, timeout=30)
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise RuntimeError(f"Mailu API 错误: {resp.status_code} {resp.text[:300]}")
        return resp.json() if resp.text else None

    def _find_domain(self, domain_name):
        return self._admin_api("GET", f"/domain/{domain_name}")

    def _find_user(self, email):
        return self._admin_api("GET", f"/user/{email}")

    def _find_alias(self, alias_email):
        return self._admin_api("GET", f"/alias/{alias_email}")

    def _find_relay(self, relay_name):
        return self._admin_api("GET", f"/relay/{relay_name}")

    def _check_email_exists(self, email_addr, subject_needle, password=None):
        import imaplib
        imap_host = os.environ.get("MAILU_IMAP_HOST", "localhost")
        imap_port = int(os.environ.get("MAILU_IMAP_PORT", "1143"))
        pw = password or self._get_mailu_tools()._get_user_password(email_addr)
        try:
            conn = imaplib.IMAP4(imap_host, imap_port)
            conn.login(email_addr, pw)
            conn.select("INBOX", readonly=True)
            status, data = conn.search(None, "ALL")
            if status != "OK":
                conn.logout()
                return False
            for mid in data[0].split():
                status, msg_data = conn.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
                if status == "OK" and msg_data[0]:
                    raw = msg_data[0][1]
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")
                    if subject_needle.lower() in raw.lower():
                        conn.logout()
                        return True
            conn.logout()
            return False
        except Exception:
            return False

    def check_outcome(self, outcome_spec):
        if not outcome_spec:
            return {"passed": True, "detail": "无 outcome_check 配置，跳过", "condition": ""}

        condition = outcome_spec.get("condition", "")

        if condition == "domain_exists":
            domain_name = outcome_spec.get("domain", "")
            domain = self._find_domain(domain_name)
            result = {
                "passed": domain is not None,
                "detail": f"域名 {domain_name} {'存在' if domain is not None else '不存在'}",
            }
        elif condition == "domain_absent":
            domain_name = outcome_spec.get("domain", "")
            domain = self._find_domain(domain_name)
            result = {
                "passed": domain is None,
                "detail": f"域名 {domain_name} {'不存在（符合预期）' if domain is None else '仍然存在'}",
            }
        elif condition == "user_exists":
            email = outcome_spec.get("email", "")
            user = self._find_user(email)
            result = {
                "passed": user is not None,
                "detail": f"用户 {email} {'存在' if user is not None else '不存在'}",
            }
        elif condition == "user_absent":
            email = outcome_spec.get("email", "")
            user = self._find_user(email)
            result = {
                "passed": user is None,
                "detail": f"用户 {email} {'不存在（符合预期）' if user is None else '仍然存在'}",
            }
        elif condition == "user_enabled":
            email = outcome_spec.get("email", "")
            expected = bool(outcome_spec.get("expected_enabled", True))
            user = self._find_user(email)
            actual = bool((user or {}).get("enabled", False))
            result = {
                "passed": user is not None and actual == expected,
                "detail": f"用户 {email} 启用状态为 {actual}，预期 {expected}",
            }
        elif condition == "alias_exists":
            alias_email = outcome_spec.get("alias_email", "")
            alias = self._find_alias(alias_email)
            result = {
                "passed": alias is not None,
                "detail": f"别名 {alias_email} {'存在' if alias is not None else '不存在'}",
            }
        elif condition == "alias_absent":
            alias_email = outcome_spec.get("alias_email", "")
            alias = self._find_alias(alias_email)
            result = {
                "passed": alias is None,
                "detail": f"别名 {alias_email} {'不存在（符合预期）' if alias is None else '仍然存在'}",
            }
        elif condition == "alias_destination":
            alias_email = outcome_spec.get("alias_email", "")
            expected_dest = outcome_spec.get("expected_destination", "")
            alias = self._find_alias(alias_email)
            actual_dest = ",".join(sorted((alias or {}).get("destination", [])))
            expected_sorted = ",".join(sorted(d.strip() for d in expected_dest.split(",")))
            result = {
                "passed": alias is not None and actual_dest == expected_sorted,
                "detail": f"别名 {alias_email} 目标为 {actual_dest}，预期 {expected_sorted}",
            }
        elif condition == "email_subject_exists":
            email_addr = outcome_spec.get("email", "")
            subject = outcome_spec.get("subject", "")
            found = self._check_email_exists(email_addr, subject)
            result = {
                "passed": found,
                "detail": f"用户 {email_addr} 收件箱{'包含' if found else '不包含'}主题含 '{subject}' 的邮件",
            }
        elif condition == "relay_exists":
            relay_name = outcome_spec.get("relay_name", "")
            relay = self._find_relay(relay_name)
            result = {
                "passed": relay is not None,
                "detail": f"中继 {relay_name} {'存在' if relay is not None else '不存在'}",
            }
        elif condition == "relay_absent":
            relay_name = outcome_spec.get("relay_name", "")
            relay = self._find_relay(relay_name)
            result = {
                "passed": relay is None,
                "detail": f"中继 {relay_name} {'不存在（符合预期）' if relay is None else '仍然存在'}",
            }
        else:
            return {"passed": False, "detail": f"未知 outcome condition: {condition}", "condition": condition}

        result["condition"] = condition
        return result
