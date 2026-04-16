import os
import subprocess

from .backend_abc import EnvironmentBackend
from .settings import REPO_ROOT, reload_runtime_env


class ZammadBackend(EnvironmentBackend):
    def __init__(self):
        self._zammad_tools = None

    def _get_zammad_tools(self):
        if self._zammad_tools is not None:
            return self._zammad_tools
        try:
            from . import zammad_tools as zammad_tools_module
        except ModuleNotFoundError as exc:
            raise RuntimeError("The current environment is missing the zammad_tools module.") from exc
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

    def reset(self):
        script_path = os.path.join(REPO_ROOT, "scripts", "reset_zammad_env.sh")
        try:
            subprocess.run(["bash", script_path], cwd=REPO_ROOT, check=True)
            reload_runtime_env()
            print("[ZammadBackend] reset_zammad_env.sh completed")
        except Exception as exc:
            print(f"[ZammadBackend] reset_zammad_env.sh failed: {exc}")

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
            return {"passed": True, "detail": "No outcome_check configured; skipped", "condition": ""}

        condition = outcome_spec.get("condition", "")
        ticket = self._resolve_ticket(outcome_spec)
        title = outcome_spec.get("title", "")
        ticket_label = (
            f"ticket {outcome_spec.get('ticket_id')}"
            if outcome_spec.get("ticket_id") is not None
            else f"ticket titled {title}"
        )

        if condition == "ticket_exists":
            result = {
                "passed": ticket is not None,
                "detail": f"{ticket_label} {'still exists' if ticket is not None else 'does not exist'}",
            }
        elif condition == "ticket_absent":
            result = {
                "passed": ticket is None,
                "detail": f"{ticket_label} {'does not exist' if ticket is None else 'still exists'}",
            }
        elif condition == "ticket_title_exists":
            result = {
                "passed": ticket is not None,
                "detail": f"Ticket titled {title} {'exists' if ticket is not None else 'does not exist'}",
            }
        elif condition == "ticket_title_absent":
            result = {
                "passed": ticket is None,
                "detail": f"Ticket titled {title} {'does not exist' if ticket is None else 'still exists'}",
            }
        elif condition == "ticket_tag_exists":
            tags = self._ticket_tags(ticket["id"]) if ticket else []
            expected_tag = str(outcome_spec.get("tag", "")).strip().lower()
            actual_tags = tags
            result = {
                "passed": ticket is not None and expected_tag in actual_tags,
                "detail": f"Ticket tags are {actual_tags}, expected to include {expected_tag}",
            }
        elif condition == "ticket_tag_absent":
            tags = self._ticket_tags(ticket["id"]) if ticket else []
            expected_tag = str(outcome_spec.get("tag", "")).strip().lower()
            actual_tags = tags
            result = {
                "passed": ticket is not None and expected_tag not in actual_tags,
                "detail": f"Ticket tags are {actual_tags}, expected not to include {expected_tag}",
            }
        elif condition == "customer_exists":
            customer_id = outcome_spec.get("customer_id")
            customer = self._find_customer(customer_id)
            result = {
                "passed": customer is not None,
                "detail": f"Customer {customer_id} {'exists' if customer is not None else 'does not exist'}",
            }
        elif condition == "customer_email_exists":
            email = outcome_spec.get("customer_email", "")
            customer = self._find_customer_by_email(email)
            result = {
                "passed": customer is not None,
                "detail": f"Customer {email} {'exists' if customer is not None else 'does not exist'}",
            }
        elif condition == "ticket_state":
            expected = outcome_spec.get("expected_state", "")
            if not ticket:
                result = {"passed": False, "detail": f"{ticket_label} does not exist"}
            else:
                states = self._states_by_id()
                actual = (states.get(ticket.get("state_id")) or {}).get("name", "")
                result = {"passed": actual == expected, "detail": f"Ticket state is {actual or 'missing'}, expected {expected}"}
        elif condition == "ticket_article_count":
            if not ticket:
                result = {"passed": False, "detail": f"{ticket_label} does not exist"}
            else:
                articles = self._api_json("GET", f"ticket_articles/by_ticket/{ticket['id']}") or []
                actual = len(articles)
                expected = outcome_spec.get("expected_count", 0)
                result = {"passed": actual == expected, "detail": f"Ticket article count is {actual}, expected {expected}"}
        elif condition == "ticket_group":
            if not ticket:
                result = {"passed": False, "detail": f"{ticket_label} does not exist"}
            else:
                groups = self._groups_by_id()
                actual = (groups.get(ticket.get("group_id")) or {}).get("name", "")
                expected = outcome_spec.get("expected_group", "")
                result = {"passed": actual == expected, "detail": f"Ticket group is {actual or 'missing'}, expected {expected}"}
        elif condition == "ticket_priority":
            if not ticket:
                result = {"passed": False, "detail": f"{ticket_label} does not exist"}
            else:
                priorities = self._priorities_by_id()
                actual = (priorities.get(ticket.get("priority_id")) or {}).get("name", "")
                expected = outcome_spec.get("expected_priority", "")
                result = {"passed": actual == expected, "detail": f"Ticket priority is {actual or 'missing'}, expected {expected}"}
        elif condition == "ticket_owner_email":
            if not ticket:
                result = {"passed": False, "detail": f"{ticket_label} does not exist"}
            else:
                owner = self._find_customer(ticket.get("owner_id"))
                actual = str((owner or {}).get("email", "")).strip().lower()
                expected = str(outcome_spec.get("expected_owner_email", "")).strip().lower()
                result = {"passed": actual == expected, "detail": f"Ticket owner is {actual or 'missing'}, expected {expected}"}
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
            result = {"passed": actual == expected, "detail": f"Open ticket count is {actual}, expected {expected}"}
        else:
            return {"passed": False, "detail": f"Unknown outcome condition: {condition}", "condition": condition}

        result["condition"] = condition
        return result
