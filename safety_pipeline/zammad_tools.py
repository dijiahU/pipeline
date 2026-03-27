"""
Zammad REST API 工具注册。

使用 Basic Auth 调用 Zammad 官方 API。
"""

import json
import os

from .exceptions import ToolExecutionError
from .service_tools import ServiceToolRegistry

try:
    import requests
    from requests.auth import HTTPBasicAuth
except ModuleNotFoundError:
    requests = None
    HTTPBasicAuth = None


_config = {
    "base_url": os.environ.get("ZAMMAD_BASE_URL", "http://localhost:8081").rstrip("/"),
    "admin_user": os.environ.get("ZAMMAD_ADMIN_USER", "admin@example.com"),
    "admin_password": os.environ.get("ZAMMAD_ADMIN_PASSWORD", "Admin123!"),
}

_REGISTRY = ServiceToolRegistry(service_id="zammad")


def zammad_tool(name, description, params, required=None, is_write=False):
    return _REGISTRY.register(
        name=name,
        description=description,
        params=params,
        required=required,
        is_write=is_write,
    )


def get_all_schemas():
    return _REGISTRY.get_all_schemas()


def call_tool(name, args):
    return _REGISTRY.call_tool(name, args)


def get_tool_names():
    return _REGISTRY.get_tool_names()


def get_write_tool_names():
    return _REGISTRY.get_write_tool_names()


def get_tool_summary():
    return _REGISTRY.get_tool_summary()


def _require_requests():
    if requests is None:
        raise ToolExecutionError("requests 库未安装。pip install requests")


def _auth():
    return HTTPBasicAuth(_config["admin_user"], _config["admin_password"])


def _api(method, path, **kwargs):
    _require_requests()
    url = f"{_config['base_url']}/api/v1/{path.lstrip('/')}"
    kwargs.setdefault("auth", _auth())
    kwargs.setdefault("timeout", 30)
    kwargs.setdefault("headers", {})
    headers = kwargs["headers"]
    if "Content-Type" not in headers and method.upper() in {"POST", "PUT", "PATCH"}:
        headers["Content-Type"] = "application/json"
    try:
        return requests.request(method, url, **kwargs)
    except requests.RequestException as exc:
        raise ToolExecutionError(f"[Zammad 请求失败] {type(exc).__name__}: {exc}") from exc


def _api_json(method, path, **kwargs):
    resp = _api(method, path, **kwargs)
    if resp.status_code >= 400:
        raise ToolExecutionError(f"[Zammad API 错误] {resp.status_code}: {resp.text[:500]}")
    if not resp.text:
        return None
    try:
        return resp.json()
    except Exception:
        return resp.text[:1000]


def _format_json(data):
    if isinstance(data, str):
        return data
    return json.dumps(data, ensure_ascii=False, indent=2)


def _roles_by_id():
    roles = _api_json("GET", "roles") or []
    return {role["id"]: role.get("name", "") for role in roles if isinstance(role, dict) and role.get("id") is not None}


def _users_by_id():
    users = _api_json("GET", "users") or []
    return {user["id"]: user for user in users if isinstance(user, dict) and user.get("id") is not None}


def _groups_by_id():
    groups = _api_json("GET", "groups") or []
    return {group["id"]: group for group in groups if isinstance(group, dict) and group.get("id") is not None}


def _states_by_id():
    states = _api_json("GET", "ticket_states") or []
    return {state["id"]: state for state in states if isinstance(state, dict) and state.get("id") is not None}


def _find_user_by_email(email):
    for user in _api_json("GET", "users") or []:
        if str(user.get("email", "")).lower() == email.lower():
            return user
    return None


@zammad_tool(
    "list_customers",
    "列出客户支持系统中的客户。",
    {},
)
def list_customers():
    roles = _roles_by_id()
    customers = []
    for user in _api_json("GET", "users") or []:
        role_names = [roles.get(role_id, "") for role_id in user.get("role_ids", [])]
        if "Customer" not in role_names:
            continue
        customers.append(
            {
                "id": user.get("id"),
                "firstname": user.get("firstname", ""),
                "lastname": user.get("lastname", ""),
                "email": user.get("email", ""),
                "organization_id": user.get("organization_id"),
            }
        )
    return _format_json(customers)


@zammad_tool(
    "list_tickets",
    "列出工单，可按状态、分组或客户邮箱筛选。",
    {
        "state": {"type": "string", "description": "工单状态，如 open、pending closed、closed"},
        "group": {"type": "string", "description": "工单分组，如 Billing、Support"},
        "customer_email": {"type": "string", "description": "客户邮箱"},
    },
)
def list_tickets(state="", group="", customer_email=""):
    groups = _groups_by_id()
    states = _states_by_id()
    users = _users_by_id()
    results = []
    for ticket in _api_json("GET", "tickets") or []:
        group_name = (groups.get(ticket.get("group_id")) or {}).get("name", "")
        state_name = (states.get(ticket.get("state_id")) or {}).get("name", "")
        customer = users.get(ticket.get("customer_id")) or {}
        if state and state_name.lower() != state.lower():
            continue
        if group and group_name.lower() != group.lower():
            continue
        if customer_email and str(customer.get("email", "")).lower() != customer_email.lower():
            continue
        results.append(
            {
                "id": ticket.get("id"),
                "number": ticket.get("number", ""),
                "title": ticket.get("title", ""),
                "state": state_name,
                "group": group_name,
                "customer_email": customer.get("email", ""),
                "article_count": ticket.get("article_count", 0),
            }
        )
    return _format_json(results)


@zammad_tool(
    "get_ticket",
    "获取单个工单详情。",
    {
        "ticket_id": {"type": "integer", "description": "工单 ID"},
    },
)
def get_ticket(ticket_id):
    ticket = _api_json("GET", f"tickets/{ticket_id}")
    groups = _groups_by_id()
    states = _states_by_id()
    users = _users_by_id()
    articles = _api_json("GET", f"ticket_articles/by_ticket/{ticket_id}") or []
    customer = users.get(ticket.get("customer_id")) or {}
    return _format_json(
        {
            "id": ticket.get("id"),
            "number": ticket.get("number", ""),
            "title": ticket.get("title", ""),
            "state": (states.get(ticket.get("state_id")) or {}).get("name", ""),
            "group": (groups.get(ticket.get("group_id")) or {}).get("name", ""),
            "customer": {
                "id": customer.get("id"),
                "email": customer.get("email", ""),
                "firstname": customer.get("firstname", ""),
                "lastname": customer.get("lastname", ""),
            },
            "article_count": len(articles),
            "articles": [
                {
                    "id": article.get("id"),
                    "subject": article.get("subject"),
                    "body": article.get("body", ""),
                    "internal": article.get("internal", False),
                    "sender": article.get("sender", ""),
                    "type": article.get("type", ""),
                }
                for article in articles
            ],
        }
    )


@zammad_tool(
    "update_ticket_state",
    "更新工单状态。",
    {
        "ticket_id": {"type": "integer", "description": "工单 ID"},
        "state": {"type": "string", "description": "新状态，如 open、pending closed、closed"},
    },
    is_write=True,
)
def update_ticket_state(ticket_id, state):
    data = _api_json("PUT", f"tickets/{ticket_id}", json={"state": state})
    return _format_json({"ticket_id": data.get("id"), "state": state})


@zammad_tool(
    "add_ticket_note",
    "为工单追加一条备注。",
    {
        "ticket_id": {"type": "integer", "description": "工单 ID"},
        "body": {"type": "string", "description": "备注内容"},
        "internal": {"type": "boolean", "description": "是否为内部备注"},
        "subject": {"type": "string", "description": "备注主题"},
    },
    required=["ticket_id", "body"],
    is_write=True,
)
def add_ticket_note(ticket_id, body, internal=True, subject="Internal note"):
    data = _api_json(
        "POST",
        "ticket_articles",
        json={
            "ticket_id": ticket_id,
            "subject": subject,
            "body": body,
            "content_type": "text/plain",
            "type": "note",
            "internal": bool(internal),
            "sender": "Agent",
        },
    )
    return _format_json(
        {
            "id": data.get("id"),
            "ticket_id": data.get("ticket_id"),
            "subject": data.get("subject", ""),
            "internal": data.get("internal", False),
        }
    )


@zammad_tool(
    "delete_ticket",
    "永久删除工单。",
    {
        "ticket_id": {"type": "integer", "description": "工单 ID"},
    },
    is_write=True,
)
def delete_ticket(ticket_id):
    resp = _api("DELETE", f"tickets/{ticket_id}")
    if resp.status_code >= 400:
        raise ToolExecutionError(f"[Zammad API 错误] {resp.status_code}: {resp.text[:500]}")
    return _format_json({"deleted_ticket_id": ticket_id})


# ---------------------------------------------------------------------------
# New tools based on Zammad REST API v1
# ---------------------------------------------------------------------------

@zammad_tool(
    "get_customer",
    "获取单个客户的详细信息。",
    {
        "customer_id": {"type": "integer", "description": "客户 ID"},
    },
)
def get_customer(customer_id):
    user = _api_json("GET", f"users/{customer_id}")
    roles = _roles_by_id()
    role_names = [roles.get(rid, "") for rid in user.get("role_ids", [])]
    return _format_json({
        "id": user.get("id"),
        "firstname": user.get("firstname", ""),
        "lastname": user.get("lastname", ""),
        "email": user.get("email", ""),
        "phone": user.get("phone", ""),
        "organization_id": user.get("organization_id"),
        "roles": role_names,
        "active": user.get("active", True),
        "note": user.get("note", ""),
    })


@zammad_tool(
    "search_tickets",
    "全文搜索工单。",
    {
        "query": {"type": "string", "description": "搜索关键词"},
        "limit": {"type": "integer", "description": "返回数量限制，默认 20"},
    },
    required=["query"],
)
def search_tickets(query, limit=20):
    data = _api_json("GET", "tickets/search", params={"query": query, "limit": limit, "expand": "true"})
    assets = data if isinstance(data, list) else (data.get("assets", {}).get("Ticket", {}).values() if isinstance(data, dict) else [])
    results = []
    for ticket in assets:
        if not isinstance(ticket, dict):
            continue
        results.append({
            "id": ticket.get("id"),
            "number": ticket.get("number", ""),
            "title": ticket.get("title", ""),
            "state": ticket.get("state", ""),
            "group": ticket.get("group", ""),
        })
    return _format_json(results[:limit])


@zammad_tool(
    "list_ticket_articles",
    "获取指定工单的所有文章/回复。",
    {
        "ticket_id": {"type": "integer", "description": "工单 ID"},
    },
)
def list_ticket_articles(ticket_id):
    articles = _api_json("GET", f"ticket_articles/by_ticket/{ticket_id}") or []
    results = []
    for article in articles:
        results.append({
            "id": article.get("id"),
            "ticket_id": article.get("ticket_id"),
            "subject": article.get("subject"),
            "body": article.get("body", ""),
            "internal": article.get("internal", False),
            "sender": article.get("sender", ""),
            "type": article.get("type", ""),
            "created_at": article.get("created_at", ""),
        })
    return _format_json(results)


@zammad_tool(
    "create_ticket",
    "创建一个新工单。",
    {
        "title": {"type": "string", "description": "工单标题"},
        "group": {"type": "string", "description": "工单分组，如 Users"},
        "customer_email": {"type": "string", "description": "客户邮箱"},
        "body": {"type": "string", "description": "工单初始内容"},
        "priority": {"type": "string", "description": "优先级，如 1 low、2 normal、3 high"},
    },
    required=["title", "group", "body"],
    is_write=True,
)
def create_ticket(title, group, body, customer_email="", priority="2 normal"):
    payload = {
        "title": title,
        "group": group,
        "priority": priority,
        "article": {
            "subject": title,
            "body": body,
            "type": "note",
            "internal": False,
        },
    }
    if customer_email:
        payload["customer"] = customer_email
    data = _api_json("POST", "tickets", json=payload)
    return _format_json({
        "id": data.get("id"),
        "number": data.get("number", ""),
        "title": data.get("title", ""),
    })


@zammad_tool(
    "update_ticket",
    "更新工单属性（标题、优先级、分组、负责人等）。",
    {
        "ticket_id": {"type": "integer", "description": "工单 ID"},
        "title": {"type": "string", "description": "新标题"},
        "group": {"type": "string", "description": "新分组"},
        "priority": {"type": "string", "description": "新优先级，如 1 low、2 normal、3 high"},
        "owner_email": {"type": "string", "description": "新负责人邮箱"},
    },
    required=["ticket_id"],
    is_write=True,
)
def update_ticket(ticket_id, title="", group="", priority="", owner_email=""):
    payload = {}
    if title:
        payload["title"] = title
    if group:
        payload["group"] = group
    if priority:
        payload["priority"] = priority
    if owner_email:
        user = _find_user_by_email(owner_email)
        if not user:
            raise ToolExecutionError(f"[错误] 找不到用户: {owner_email}")
        payload["owner_id"] = user["id"]
    if not payload:
        raise ToolExecutionError("[错误] 至少需要提供一个要更新的字段")
    data = _api_json("PUT", f"tickets/{ticket_id}", json=payload)
    return _format_json({"ticket_id": data.get("id"), "updated_fields": list(payload.keys())})


@zammad_tool(
    "add_ticket_tag",
    "为工单添加标签。",
    {
        "ticket_id": {"type": "integer", "description": "工单 ID"},
        "tag": {"type": "string", "description": "标签名称"},
    },
    required=["ticket_id", "tag"],
    is_write=True,
)
def add_ticket_tag(ticket_id, tag):
    _api_json("POST", "tags/add", json={
        "object": "Ticket",
        "o_id": ticket_id,
        "item": tag,
    })
    return _format_json({"ticket_id": ticket_id, "tag": tag})
