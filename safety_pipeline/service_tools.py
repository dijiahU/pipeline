"""
服务工具注册抽象。

每个服务的 tools 模块都应暴露统一接口：
- get_all_schemas()
- get_tool_names()
- get_write_tool_names()
- call_tool(name, args)

这样 runtime / environment 只依赖通用 provider，不依赖具体服务的注册细节。
"""

from dataclasses import dataclass

from .exceptions import ToolExecutionError


_SERVICE_TOOL_GROUPS = {
    "gitea": {
        "repo_info": {
            "desc": "Repository metadata, settings, tags, and project-level inspection.",
            "tools": {"list_projects", "get_project", "get_repo_settings", "list_repo_tags"},
        },
        "repo_content": {
            "desc": "Repository file and directory reads.",
            "tools": {"read_repo_file", "list_repo_directory"},
        },
        "branch_ops": {
            "desc": "Branch listing, deletion, and protection inspection or updates.",
            "tools": {"list_branches", "create_branch", "get_branch_protection", "delete_branch", "update_branch_protection"},
        },
        "issue_tracking": {
            "desc": "Issue discovery, detail reads, comments, creation, and lifecycle changes.",
            "tools": {
                "list_issues",
                "get_issue",
                "list_issue_comments",
                "create_issue",
                "add_issue_comment",
                "close_issue",
                "reopen_issue",
            },
        },
        "pull_requests": {
            "desc": "Pull request listing, detail reads, file inspection, and creation.",
            "tools": {"list_merge_requests", "get_pull_request", "list_pull_request_files", "create_pull_request"},
        },
        "ci_cd": {
            "desc": "Pipeline monitoring and CI log inspection.",
            "tools": {"read_pipeline_log", "list_pipeline_jobs", "get_latest_pipeline_log"},
        },
        "access_control": {
            "desc": "Collaborators and deploy key management.",
            "tools": {
                "list_collaborators",
                "add_collaborator",
                "remove_collaborator",
                "list_deploy_keys",
                "add_deploy_key",
                "remove_deploy_key",
            },
        },
        "labels_and_milestones": {
            "desc": "Repository planning metadata such as labels and milestones.",
            "tools": {
                "list_repo_labels",
                "create_label",
                "list_milestones",
                "create_milestone",
            },
        },
        "releases": {
            "desc": "Release listing and publishing operations.",
            "tools": {"list_releases", "create_release", "delete_release"},
        },
        "webhooks": {
            "desc": "Webhook inspection and management.",
            "tools": {"list_webhooks", "create_webhook", "delete_webhook"},
        },
    },
    "rocketchat": {
        "channels": {
            "desc": "Channel discovery, creation, updates, archival, and deletion.",
            "tools": {
                "list_channels",
                "get_channel_info",
                "create_channel",
                "delete_channel",
                "set_channel_topic",
                "archive_channel",
            },
        },
        "messages": {
            "desc": "Read or mutate channel messages.",
            "tools": {"list_channel_messages", "send_message", "delete_message"},
        },
        "users": {
            "desc": "User discovery, profile reads, and account removal.",
            "tools": {"list_users", "get_user_info", "delete_user"},
        },
    },
    "owncloud": {
        "file_browse": {
            "desc": "Read-only file and folder inspection.",
            "tools": {"list_files", "read_file", "file_info"},
        },
        "file_ops": {
            "desc": "Create, upload, move, copy, and delete filesystem objects.",
            "tools": {"create_folder", "upload_file", "delete_path", "move_path", "copy_path"},
        },
        "sharing": {
            "desc": "Inspect and manage file shares or public links.",
            "tools": {"list_shares", "create_share", "delete_share"},
        },
    },
    "nocodb": {
        "schema": {
            "desc": "Base and table-level schema inspection or deletion.",
            "tools": {"list_bases", "list_tables", "get_table", "delete_table"},
        },
        "records": {
            "desc": "Record listing, reads, creation, updates, and deletion.",
            "tools": {"list_records", "get_record", "create_record", "update_record", "delete_record"},
        },
        "bulk_ops": {
            "desc": "High-impact bulk record operations.",
            "tools": {"bulk_delete_records"},
        },
    },
    "zammad": {
        "customers": {
            "desc": "Customer listing and detail lookup.",
            "tools": {"list_customers", "get_customer"},
        },
        "tickets": {
            "desc": "Ticket listing, detail reads, creation, updates, and deletion.",
            "tools": {"list_tickets", "get_ticket", "create_ticket", "update_ticket", "delete_ticket", "update_ticket_state"},
        },
        "ticket_articles": {
            "desc": "Ticket conversation history and notes.",
            "tools": {"list_ticket_articles", "add_ticket_note"},
        },
        "search": {
            "desc": "Search tickets by text or filters.",
            "tools": {"search_tickets"},
        },
        "tagging": {
            "desc": "Ticket tag management.",
            "tools": {"add_ticket_tag"},
        },
    },
    "erpnext": {
        "invoices": {
            "desc": "Invoice listing, detail reads, status updates, and comments.",
            "tools": {
                "list_invoices",
                "get_invoice",
                "add_invoice_comment",
                "update_invoice_status",
                "create_invoice",
                "cancel_invoice",
            },
        },
        "payments": {
            "desc": "Payment entry listing, reads, creation, and deletion.",
            "tools": {"list_payments", "get_payment", "create_payment_entry", "delete_payment_entry"},
        },
        "customers": {
            "desc": "Customer listing and detail reads.",
            "tools": {"list_customers", "get_customer"},
        },
    },
    "openemr": {
        "patients": {
            "desc": "Patient listing, detail reads, updates, and deletion.",
            "tools": {"list_patients", "get_patient", "update_patient", "delete_patient"},
        },
        "appointments": {
            "desc": "Appointment listing, detail reads, creation, and rescheduling.",
            "tools": {"list_appointments", "get_appointment", "create_appointment", "reschedule_appointment"},
        },
        "clinical_notes": {
            "desc": "Encounter history and patient note management.",
            "tools": {"list_encounters", "add_patient_note"},
        },
        "allergies": {
            "desc": "Patient allergy inspection and updates.",
            "tools": {"list_patient_allergies", "add_allergy"},
        },
    },
    "discourse": {
        "topics": {
            "desc": "Topic discovery, replies, creation, moderation, and closure.",
            "tools": {
                "list_topics",
                "get_topic",
                "search_topics",
                "create_topic",
                "create_post",
                "set_topic_pinned",
                "close_topic",
                "delete_topic",
            },
        },
        "users": {
            "desc": "User listing, profile reads, and suspension.",
            "tools": {"list_users", "get_user", "suspend_user"},
        },
        "categories": {
            "desc": "Category listing and navigation context.",
            "tools": {"list_categories"},
        },
    },
}


def _normalize_short_text(text, limit=80):
    value = " ".join(str(text or "").replace("\n", " ").split()).strip()
    if not value:
        return ""
    value = value.rstrip("。.;；")
    if len(value) <= limit:
        return value
    return f"{value[:limit - 3].rstrip()}..."


def _format_group_name(group_name):
    return str(group_name or "general").strip() or "general"


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    schema: dict
    handler: object
    is_write: bool
    group: str = ""
    short_description: str = ""


class ServiceToolRegistry:
    def __init__(self, service_id):
        self.service_id = service_id
        self._tools = {}

    def _infer_group(self, tool_name, is_write):
        service_groups = _SERVICE_TOOL_GROUPS.get(self.service_id, {})
        for group_name, meta in service_groups.items():
            if tool_name in meta.get("tools", set()):
                return group_name
        if tool_name.startswith(("list_", "get_", "read_", "search_")) and not is_write:
            return "read_ops"
        if is_write:
            return "write_ops"
        return "general"

    def _describe_group(self, group_name):
        service_groups = _SERVICE_TOOL_GROUPS.get(self.service_id, {})
        meta = service_groups.get(group_name, {})
        if meta.get("desc"):
            return meta["desc"]
        if group_name == "general":
            return "General tools for this service."
        if group_name == "read_ops":
            return "Read-only inspection tools."
        if group_name == "write_ops":
            return "Write or mutating tools."
        return _normalize_short_text(group_name.replace("_", " ").title(), limit=120)

    def register(
        self,
        name,
        description,
        params,
        required=None,
        is_write=False,
        group="",
        short_description="",
    ):
        def decorator(func):
            if required is None:
                import inspect

                sig = inspect.signature(func)
                req = [
                    p for p, v in sig.parameters.items()
                    if v.default is inspect.Parameter.empty
                ]
            else:
                req = list(required)

            normalized_group = _format_group_name(group or self._infer_group(name, bool(is_write)))
            normalized_short_description = _normalize_short_text(
                short_description or description,
            )
            schema = {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": params,
                        "required": req,
                    },
                },
            }
            self._tools[name] = RegisteredTool(
                name=name,
                schema=schema,
                handler=func,
                is_write=bool(is_write),
                group=normalized_group,
                short_description=normalized_short_description,
            )
            return func

        return decorator

    def get_all_schemas(self):
        return [tool.schema for tool in self._tools.values()]

    def get_tool_names(self):
        return list(self._tools.keys())

    def get_write_tool_names(self):
        return [tool.name for tool in self._tools.values() if tool.is_write]

    def get_tool_groups(self):
        groups = {}
        for tool in self._tools.values():
            group_name = _format_group_name(tool.group)
            if group_name not in groups:
                groups[group_name] = {
                    "name": group_name,
                    "description": self._describe_group(group_name),
                    "tools": [],
                }
            groups[group_name]["tools"].append(tool.name)
        return groups

    def get_tool_groups_summary(self):
        return [
            {
                "group": group["name"],
                "desc": group["description"],
                "count": len(group["tools"]),
            }
            for group in self.get_tool_groups().values()
        ]

    def get_tools_in_group(self, group_name):
        normalized_group = _format_group_name(group_name)
        return [
            {
                "name": tool.name,
                "desc": tool.short_description or _normalize_short_text(tool.schema["function"].get("description", "")),
                "is_write": tool.is_write,
            }
            for tool in self._tools.values()
            if _format_group_name(tool.group) == normalized_group
        ]

    def get_tool_summary(self):
        return [
            {
                "name": tool.name,
                "is_write": tool.is_write,
                "description": tool.schema["function"].get("description", ""),
                "group": _format_group_name(tool.group),
                "group_description": self._describe_group(tool.group),
                "short_description": tool.short_description or _normalize_short_text(tool.schema["function"].get("description", "")),
            }
            for tool in self._tools.values()
        ]

    def call_tool(self, name, args):
        tool = self._tools.get(name)
        if not tool:
            raise ToolExecutionError(f"[错误] 未知 tool: {name}")
        try:
            return tool.handler(**args)
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError(f"[执行出错] {type(exc).__name__}: {exc}") from exc
