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
            "desc": "Public channel discovery, membership changes, and metadata updates.",
            "tools": {
                "list_channels",
                "get_channel_info",
                "list_channel_members",
                "create_channel",
                "invite_user_to_channel",
                "remove_user_from_channel",
                "set_channel_topic",
                "set_channel_description",
                "archive_channel",
                "delete_channel",
            },
        },
        "private_channels": {
            "desc": "Private channel discovery, membership changes, and metadata updates.",
            "tools": {
                "list_private_channels",
                "get_private_channel_info",
                "list_private_channel_members",
                "create_private_channel",
                "invite_user_to_private_channel",
                "remove_user_from_private_channel",
                "set_private_channel_topic",
                "set_private_channel_description",
                "archive_private_channel",
                "delete_private_channel",
            },
        },
        "messages": {
            "desc": "Room message reads, thread inspection, posting, pinning, and deletion.",
            "tools": {
                "list_channel_messages",
                "get_message",
                "list_thread_messages",
                "send_message",
                "send_thread_reply",
                "pin_message",
                "unpin_message",
                "delete_message",
            },
        },
        "direct_messages": {
            "desc": "Direct message room creation and DM history access.",
            "tools": {
                "list_direct_messages",
                "list_direct_message_messages",
                "create_direct_message",
                "send_direct_message",
            },
        },
        "users": {
            "desc": "User discovery, profile reads, account creation, and activation changes.",
            "tools": {
                "list_users",
                "get_user_info",
                "create_user",
                "set_user_active_status",
                "delete_user",
            },
        },
        "integrations": {
            "desc": "Incoming and outgoing webhook integrations.",
            "tools": {
                "list_integrations",
                "get_integration",
                "create_incoming_integration",
                "create_outgoing_integration",
                "remove_integration",
            },
        },
        "moderation": {
            "desc": "High-impact moderation and destructive workspace actions.",
            "tools": {
                "archive_channel",
                "archive_private_channel",
                "delete_channel",
                "delete_private_channel",
                "delete_message",
                "delete_user",
                "remove_user_from_channel",
                "remove_user_from_private_channel",
                "set_user_active_status",
            },
        },
    },
    "owncloud": {
        "file_browse": {
            "desc": "Read-only file and folder inspection, recursive browse, and search.",
            "tools": {"list_files", "list_directory_tree", "read_file", "file_info", "search_files"},
        },
        "file_ops": {
            "desc": "Create, upload, rename, move, copy, and delete filesystem objects.",
            "tools": {"create_folder", "upload_file", "delete_path", "move_path", "rename_path", "copy_path"},
        },
        "sharing": {
            "desc": "Inspect and manage public links or internal shares.",
            "tools": {
                "list_shares",
                "get_share",
                "list_public_links",
                "list_user_shares",
                "create_public_link",
                "create_share",
                "create_user_share",
                "update_share_permissions",
                "delete_share",
            },
        },
    },
    "nocodb": {
        "schema": {
            "desc": "Base and table-level schema inspection or creation.",
            "tools": {
                "list_bases",
                "get_base",
                "create_base",
                "list_tables",
                "get_table",
                "list_columns",
                "create_table",
            },
        },
        "records": {
            "desc": "Record listing, reads, creation, updates, and deletion.",
            "tools": {
                "list_records",
                "get_record",
                "create_record",
                "update_record",
                "update_record_by_field",
                "delete_record",
            },
        },
        "views_queries": {
            "desc": "Filtered reads and business-key lookups.",
            "tools": {"query_records", "find_records"},
        },
        "bulk_ops": {
            "desc": "High-impact bulk record or table deletion operations.",
            "tools": {"bulk_delete_records", "delete_table"},
        },
    },
    "zammad": {
        "customers": {
            "desc": "Customer directory lookups and customer-specific ticket reads.",
            "tools": {"list_customers", "get_customer", "list_customer_tickets"},
        },
        "agents_and_groups": {
            "desc": "Agent roster and support group inspection.",
            "tools": {"list_agents", "get_agent", "list_groups", "get_group"},
        },
        "ticket_queries": {
            "desc": "Ticket lists, filtered searches, detail reads, and workflow metadata.",
            "tools": {
                "list_tickets",
                "list_group_tickets",
                "list_agent_tickets",
                "get_ticket",
                "search_tickets",
                "list_ticket_states",
                "list_ticket_priorities",
            },
        },
        "ticket_articles": {
            "desc": "Ticket conversation history plus internal notes or public replies.",
            "tools": {"list_ticket_articles", "add_ticket_note", "add_public_ticket_reply"},
        },
        "ticket_creation": {
            "desc": "Create new support tickets for existing customers.",
            "tools": {"create_ticket"},
        },
        "ticket_updates": {
            "desc": "Ticket metadata updates such as title, state, and priority.",
            "tools": {
                "update_ticket",
                "update_ticket_state",
                "rename_ticket",
                "update_ticket_priority",
            },
        },
        "ticket_assignment": {
            "desc": "Move tickets between groups or assign a specific owner.",
            "tools": {"reassign_ticket_owner", "move_ticket_to_group"},
        },
        "tagging": {
            "desc": "Inspect, add, or remove ticket tags.",
            "tools": {"list_ticket_tags", "add_ticket_tag", "remove_ticket_tag"},
        },
        "destructive_ops": {
            "desc": "Permanent ticket deletion and other high-impact operations.",
            "tools": {"delete_ticket"},
        },
    },
    "erpnext": {
        "invoices": {
            "desc": "Invoice lists, customer-specific reads, overdue inspection, and invoice creation.",
            "tools": {
                "list_invoices",
                "list_customer_invoices",
                "list_overdue_invoices",
                "get_invoice",
                "create_invoice",
            },
        },
        "invoice_comments": {
            "desc": "Invoice comment history inspection and note creation.",
            "tools": {"list_invoice_comments", "add_invoice_comment"},
        },
        "invoice_updates": {
            "desc": "Financial state or due-date changes applied to existing invoices.",
            "tools": {"update_invoice_due_date", "update_invoice_status", "cancel_invoice"},
        },
        "payments": {
            "desc": "Payment entry reads, invoice-linked payment inspection, creation, and deletion.",
            "tools": {
                "list_payments",
                "list_invoice_payments",
                "get_payment",
                "create_payment_entry",
                "delete_payment_entry",
            },
        },
        "customers": {
            "desc": "Customer directory reads plus customer master creation.",
            "tools": {"list_customers", "get_customer", "create_customer"},
        },
        "suppliers": {
            "desc": "Supplier directory reads plus supplier master creation.",
            "tools": {"list_suppliers", "get_supplier", "create_supplier"},
        },
        "items_catalog": {
            "desc": "Item catalog reads and product master creation.",
            "tools": {"list_items", "get_item", "create_item"},
        },
        "purchase_invoices": {
            "desc": "Purchase invoice lists, supplier-specific reads, overdue inspection, and purchase invoice creation.",
            "tools": {
                "list_purchase_invoices",
                "list_supplier_purchase_invoices",
                "list_overdue_purchase_invoices",
                "get_purchase_invoice",
                "create_purchase_invoice",
            },
        },
        "purchase_invoice_comments": {
            "desc": "Purchase invoice comment history inspection and note creation.",
            "tools": {"list_purchase_invoice_comments", "add_purchase_invoice_comment"},
        },
        "purchase_invoice_updates": {
            "desc": "Due-date changes or cancellation applied to existing purchase invoices.",
            "tools": {"update_purchase_invoice_due_date", "cancel_purchase_invoice"},
        },
        "reference_data": {
            "desc": "Reference metadata such as companies and payment modes.",
            "tools": {"list_companies", "list_payment_modes"},
        },
    },
    "openemr": {
        "patients": {
            "desc": "Patient directory reads plus chart creation, demographics updates, and deletion.",
            "tools": {"list_patients", "get_patient", "create_patient", "update_patient", "delete_patient"},
        },
        "patient_notes": {
            "desc": "Patient note history inspection and routine chart-note entry.",
            "tools": {"list_patient_notes", "add_patient_note"},
        },
        "appointments": {
            "desc": "Appointment discovery for patients or providers, plus detail reads and creation.",
            "tools": {
                "list_appointments",
                "get_appointment",
                "list_patient_appointments",
                "list_provider_appointments",
                "create_appointment",
            },
        },
        "appointment_updates": {
            "desc": "Operational appointment changes such as rescheduling or cancellation.",
            "tools": {"reschedule_appointment", "cancel_appointment"},
        },
        "encounters": {
            "desc": "Encounter history reads plus creation or metadata updates for visits.",
            "tools": {"list_encounters", "get_encounter", "create_encounter", "update_encounter"},
        },
        "allergies": {
            "desc": "Patient allergy inspection and updates.",
            "tools": {"list_patient_allergies", "add_allergy"},
        },
        "medications": {
            "desc": "Medication list reads, new medication entries, and discontinuation workflow.",
            "tools": {"list_patient_medications", "get_medication", "add_medication", "discontinue_medication"},
        },
        "insurance": {
            "desc": "Insurance policy lookup plus routine coverage entry or termination.",
            "tools": {
                "list_patient_insurance",
                "get_insurance_policy",
                "add_insurance_policy",
                "terminate_insurance_policy",
            },
        },
    },
    "discourse": {
        "topic_discovery": {
            "desc": "Topic discovery, detail lookup, and title or search based retrieval.",
            "tools": {
                "list_topics",
                "list_open_topics",
                "list_closed_topics",
                "get_topic",
                "get_topic_by_title",
                "search_topics",
                "search_posts",
            },
        },
        "topic_posts": {
            "desc": "Topic post inspection plus routine replies.",
            "tools": {
                "list_topic_posts",
                "create_post",
            },
        },
        "topic_updates": {
            "desc": "Topic creation plus routine title or category maintenance.",
            "tools": {
                "create_topic",
                "rename_topic",
                "move_topic_to_category",
            },
        },
        "topic_moderation": {
            "desc": "Topic pinning, closure, reopening, or deletion actions.",
            "tools": {
                "set_topic_pinned",
                "unpin_topic",
                "close_topic",
                "reopen_topic",
                "delete_topic",
            },
        },
        "categories": {
            "desc": "Category listing, detail reads, and topic navigation by category.",
            "tools": {"list_categories", "get_category", "list_category_topics"},
        },
        "category_updates": {
            "desc": "Forum taxonomy changes such as adding a new category.",
            "tools": {"create_category"},
        },
        "user_directory": {
            "desc": "User listing, profile reads, and user activity lookup.",
            "tools": {
                "list_users",
                "list_staff_users",
                "get_user",
                "list_user_posts",
                "list_user_topics",
            },
        },
        "user_moderation": {
            "desc": "Administrative suspension and reinstatement actions for accounts.",
            "tools": {"suspend_user", "unsuspend_user"},
        },
    },
    "mailu": {
        "domain_management": {
            "desc": "Mail domain lifecycle: listing, creation, update, and deletion.",
            "tools": {"list_domains", "get_domain", "create_domain", "update_domain", "delete_domain"},
        },
        "user_management": {
            "desc": "Mailbox account lifecycle: listing, creation, update, password change, and deletion.",
            "tools": {"list_users", "get_user", "create_user", "update_user", "change_user_password", "delete_user"},
        },
        "alias_management": {
            "desc": "Email alias and forwarding rules: listing, creation, update, and deletion.",
            "tools": {"list_aliases", "get_alias", "create_alias", "update_alias", "delete_alias"},
        },
        "relay_management": {
            "desc": "Outbound relay host configuration and inspection.",
            "tools": {"list_relays", "get_relay", "create_relay", "delete_relay"},
        },
        "alternative_domains": {
            "desc": "Alternative domain names that map to a primary domain.",
            "tools": {"list_alternative_domains", "create_alternative_domain", "delete_alternative_domain"},
        },
        "email_read": {
            "desc": "IMAP-based mailbox folder listing and email content retrieval.",
            "tools": {"list_mailbox_folders", "list_emails", "read_email"},
        },
        "email_send": {
            "desc": "SMTP-based email sending. These operations are irreversible once executed.",
            "tools": {"send_email", "send_email_with_attachment"},
        },
        "quota_management": {
            "desc": "User storage quota inspection and adjustment.",
            "tools": {"get_user_quota", "update_user_quota"},
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
