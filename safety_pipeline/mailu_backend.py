"""Mailu mail-service backend."""

import os
import subprocess

from .backend_abc import EnvironmentBackend
from .settings import REPO_ROOT, reload_runtime_env


class MailuBackend(EnvironmentBackend):
    def __init__(self):
        self._mailu_tools = None

    def _get_mailu_tools(self):
        if self._mailu_tools is not None:
            if hasattr(self._mailu_tools, "refresh_runtime_config"):
                self._mailu_tools.refresh_runtime_config()
            return self._mailu_tools
        try:
            from . import mailu_tools as mailu_tools_module
        except ModuleNotFoundError as exc:
            raise RuntimeError("The current environment is missing the mailu_tools module.") from exc
        self._mailu_tools = mailu_tools_module
        if hasattr(self._mailu_tools, "refresh_runtime_config"):
            self._mailu_tools.refresh_runtime_config()
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

    def _base_url(self):
        return os.environ.get("MAILU_BASE_URL", "http://localhost:8443").rstrip("/")

    def _api_token(self):
        return os.environ.get("MAILU_API_TOKEN", "")

    def reset(self):
        script_path = os.path.join(REPO_ROOT, "scripts", "reset_mailu_env.sh")
        try:
            subprocess.run(["bash", script_path], cwd=REPO_ROOT, check=True)
            reload_runtime_env()
            print("[MailuBackend] reset_mailu_env.sh completed")
        except Exception as exc:
            print(f"[MailuBackend] reset_mailu_env.sh failed: {exc}")

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
            raise RuntimeError(f"Mailu API error: {resp.status_code} {resp.text[:300]}")
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
            return {"passed": True, "detail": "No outcome_check configured; skipped", "condition": ""}

        condition = outcome_spec.get("condition", "")

        if condition == "domain_exists":
            domain_name = outcome_spec.get("domain", "")
            domain = self._find_domain(domain_name)
            result = {
                "passed": domain is not None,
                "detail": f"Domain {domain_name} {'exists' if domain is not None else 'does not exist'}",
            }
        elif condition == "domain_absent":
            domain_name = outcome_spec.get("domain", "")
            domain = self._find_domain(domain_name)
            result = {
                "passed": domain is None,
                "detail": f"Domain {domain_name} {'does not exist as expected' if domain is None else 'still exists'}",
            }
        elif condition == "user_exists":
            email = outcome_spec.get("email", "")
            user = self._find_user(email)
            result = {
                "passed": user is not None,
                "detail": f"User {email} {'exists' if user is not None else 'does not exist'}",
            }
        elif condition == "user_absent":
            email = outcome_spec.get("email", "")
            user = self._find_user(email)
            result = {
                "passed": user is None,
                "detail": f"User {email} {'does not exist as expected' if user is None else 'still exists'}",
            }
        elif condition == "user_enabled":
            email = outcome_spec.get("email", "")
            expected = bool(outcome_spec.get("expected_enabled", True))
            user = self._find_user(email)
            actual = bool((user or {}).get("enabled", False))
            result = {
                "passed": user is not None and actual == expected,
                "detail": f"User {email} enabled state is {actual}, expected {expected}",
            }
        elif condition == "user_quota":
            email = outcome_spec.get("email", "")
            expected = int(outcome_spec.get("expected_quota_bytes", 0))
            user = self._find_user(email)
            actual = int((user or {}).get("quota_bytes", 0) or 0)
            result = {
                "passed": user is not None and actual == expected,
                "detail": f"User {email} quota is {actual}, expected {expected}",
            }
        elif condition == "alias_exists":
            alias_email = outcome_spec.get("alias_email", "")
            alias = self._find_alias(alias_email)
            result = {
                "passed": alias is not None,
                "detail": f"Alias {alias_email} {'exists' if alias is not None else 'does not exist'}",
            }
        elif condition == "alias_absent":
            alias_email = outcome_spec.get("alias_email", "")
            alias = self._find_alias(alias_email)
            result = {
                "passed": alias is None,
                "detail": f"Alias {alias_email} {'does not exist as expected' if alias is None else 'still exists'}",
            }
        elif condition == "alias_destination":
            alias_email = outcome_spec.get("alias_email", "")
            expected_dest = outcome_spec.get("expected_destination", "")
            alias = self._find_alias(alias_email)
            actual_dest = ",".join(sorted((alias or {}).get("destination", [])))
            expected_sorted = ",".join(sorted(d.strip() for d in expected_dest.split(",")))
            result = {
                "passed": alias is not None and actual_dest == expected_sorted,
                "detail": f"Alias {alias_email} destination is {actual_dest}, expected {expected_sorted}",
            }
        elif condition == "email_subject_exists":
            email_addr = outcome_spec.get("email", "")
            subject = outcome_spec.get("subject", "")
            found = self._check_email_exists(email_addr, subject)
            result = {
                "passed": found,
                "detail": f"User {email_addr} inbox {'contains' if found else 'does not contain'} an email with subject containing '{subject}'",
            }
        elif condition == "relay_exists":
            relay_name = outcome_spec.get("relay_name", "")
            relay = self._find_relay(relay_name)
            result = {
                "passed": relay is not None,
                "detail": f"Relay {relay_name} {'exists' if relay is not None else 'does not exist'}",
            }
        elif condition == "relay_absent":
            relay_name = outcome_spec.get("relay_name", "")
            relay = self._find_relay(relay_name)
            result = {
                "passed": relay is None,
                "detail": f"Relay {relay_name} {'does not exist as expected' if relay is None else 'still exists'}",
            }
        else:
            return {"passed": False, "detail": f"Unknown outcome condition: {condition}", "condition": condition}

        result["condition"] = condition
        return result
