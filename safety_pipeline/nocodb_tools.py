"""
NocoDB API 工具注册 — 服务化工具架构标准

本模块直接通过 HTTP 调用 NocoDB REST API v2。
公共接口（给 environment.py 调用）:
  get_all_schemas() -> list
  call_tool(name, args) -> str
  get_tool_names() -> list
  get_write_tool_names() -> list
"""

import json
import os

from .exceptions import ToolExecutionError
from .service_tools import ServiceToolRegistry

try:
    import requests
except ModuleNotFoundError:
    requests = None


_config = {
    "base_url": os.environ.get("NOCODB_BASE_URL", "http://localhost:8080"),
    "token": os.environ.get("NOCODB_API_TOKEN", ""),
    "admin_email": os.environ.get("NOCODB_ADMIN_EMAIL", "admin@example.com"),
    "admin_password": os.environ.get("NOCODB_ADMIN_PASSWORD", "Admin123!"),
}

_jwt_cache = {"token": None}

_REGISTRY = ServiceToolRegistry(service_id="nocodb")


def nocodb_tool(name, description, params, required=None, is_write=False):
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
        raise ToolExecutionError("requests 库未安装，无法调用 NocoDB API。pip install requests")


def _get_auth_token():
    """Return API token or sign in for JWT."""
    if _config["token"]:
        return _config["token"]
    if _jwt_cache["token"]:
        return _jwt_cache["token"]
    _require_requests()
    resp = requests.post(
        f"{_config['base_url'].rstrip('/')}/api/v1/auth/user/signin",
        json={"email": _config["admin_email"], "password": _config["admin_password"]},
        timeout=30,
    )
    if resp.status_code != 200:
        raise ToolExecutionError(f"NocoDB 登录失败 ({resp.status_code}): {resp.text[:300]}")
    token = resp.json().get("token", "")
    if not token:
        raise ToolExecutionError("NocoDB 登录响应中无 token")
    _jwt_cache["token"] = token
    return token


def _headers():
    token = _get_auth_token()
    h = {"Content-Type": "application/json"}
    if _config["token"]:
        h["xc-token"] = token
    else:
        h["xc-auth"] = token
    return h


def _api(method, path, **kwargs):
    _require_requests()
    url = f"{_config['base_url'].rstrip('/')}/{path.lstrip('/')}"
    try:
        return requests.request(method, url, headers=_headers(), timeout=30, **kwargs)
    except requests.RequestException as exc:
        raise ToolExecutionError(f"[NocoDB 请求失败] {type(exc).__name__}: {exc}") from exc


def _api_json(method, path, **kwargs):
    resp = _api(method, path, **kwargs)
    if resp.status_code >= 400:
        raise ToolExecutionError(f"[NocoDB API 错误] {resp.status_code}: {resp.text[:500]}")
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


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------

def _get_default_workspace_id():
    """Get the default workspace ID."""
    data = _api_json("GET", "api/v2/meta/workspaces/")
    ws_list = data.get("list", []) if isinstance(data, dict) else []
    if not ws_list:
        raise ToolExecutionError("[错误] 未找到任何 workspace")
    return ws_list[0]["id"]


@nocodb_tool(
    "list_bases",
    "列出 NocoDB 上所有数据库（base）。",
    {
        "per_page": {
            "type": "integer",
            "description": "每页返回数量，默认 20",
        },
    },
)
def list_bases(per_page=20):
    ws_id = _get_default_workspace_id()
    data = _api_json("GET", f"api/v2/meta/workspaces/{ws_id}/bases/", params={"limit": per_page})
    bases = data.get("list", []) if isinstance(data, dict) else []
    results = []
    for b in bases:
        results.append({
            "id": b.get("id", ""),
            "title": b.get("title", ""),
            "description": b.get("description", ""),
        })
    return _format_json(results)


@nocodb_tool(
    "list_tables",
    "列出指定数据库中的所有表。",
    {
        "base_id": {
            "type": "string",
            "description": "数据库（base）ID 或名称",
        },
    },
)
def list_tables(base_id):
    resolved_id = _resolve_base_id(base_id)
    data = _api_json("GET", f"api/v2/meta/bases/{resolved_id}/tables")
    tables = data.get("list", []) if isinstance(data, dict) else []
    results = []
    for t in tables:
        results.append({
            "id": t.get("id", ""),
            "title": t.get("title", ""),
            "meta": t.get("meta", {}),
        })
    return _format_json(results)


@nocodb_tool(
    "get_table",
    "获取指定表的详细信息（包括列定义）。",
    {
        "table_id": {
            "type": "string",
            "description": "表 ID 或名称（需同时提供 base_id）",
        },
        "base_id": {
            "type": "string",
            "description": "数据库 ID 或名称（当 table_id 为名称时必填）",
        },
    },
)
def get_table(table_id, base_id=""):
    resolved_id = _resolve_table_id(table_id, base_id)
    data = _api_json("GET", f"api/v2/meta/tables/{resolved_id}")
    columns = []
    for col in (data.get("columns") or []):
        columns.append({
            "id": col.get("id", ""),
            "title": col.get("title", ""),
            "uidt": col.get("uidt", ""),
            "pk": col.get("pk", False),
        })
    return _format_json({
        "id": data.get("id", ""),
        "title": data.get("title", ""),
        "columns": columns,
    })


@nocodb_tool(
    "list_records",
    "列出指定表中的记录。",
    {
        "table_id": {
            "type": "string",
            "description": "表 ID 或名称",
        },
        "base_id": {
            "type": "string",
            "description": "数据库 ID 或名称（当 table_id 为名称时必填）",
        },
        "where": {
            "type": "string",
            "description": "过滤条件，NocoDB where 语法（如 '(Status,eq,active)'）",
        },
        "sort": {
            "type": "string",
            "description": "排序字段（如 '-Salary' 降序，'Name' 升序）",
        },
        "limit": {
            "type": "integer",
            "description": "返回数量限制，默认 25",
        },
        "offset": {
            "type": "integer",
            "description": "偏移量，默认 0",
        },
    },
)
def list_records(table_id, base_id="", where="", sort="", limit=25, offset=0):
    resolved_id = _resolve_table_id(table_id, base_id)
    params = {"limit": limit, "offset": offset}
    if where:
        params["where"] = where
    if sort:
        params["sort"] = sort
    data = _api_json("GET", f"api/v2/tables/{resolved_id}/records", params=params)
    records = data.get("list", []) if isinstance(data, dict) else []
    page_info = data.get("pageInfo", {}) if isinstance(data, dict) else {}
    return _format_json({"records": records, "totalRows": page_info.get("totalRows", len(records))})


@nocodb_tool(
    "get_record",
    "获取指定表中的单条记录。",
    {
        "table_id": {
            "type": "string",
            "description": "表 ID 或名称",
        },
        "record_id": {
            "type": "string",
            "description": "记录 ID（行 ID）",
        },
        "base_id": {
            "type": "string",
            "description": "数据库 ID 或名称（当 table_id 为名称时必填）",
        },
    },
)
def get_record(table_id, record_id, base_id=""):
    resolved_id = _resolve_table_id(table_id, base_id)
    data = _api_json("GET", f"api/v2/tables/{resolved_id}/records/{record_id}")
    return _format_json(data)


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------

@nocodb_tool(
    "create_record",
    "在指定表中创建一条新记录。",
    {
        "table_id": {
            "type": "string",
            "description": "表 ID 或名称",
        },
        "base_id": {
            "type": "string",
            "description": "数据库 ID 或名称（当 table_id 为名称时必填）",
        },
        "fields": {
            "type": "object",
            "description": "记录字段键值对，如 {\"Name\": \"test\", \"Status\": \"active\"}",
        },
    },
    is_write=True,
)
def create_record(table_id, fields, base_id=""):
    resolved_id = _resolve_table_id(table_id, base_id)
    data = _api_json("POST", f"api/v2/tables/{resolved_id}/records", json=fields)
    return _format_json(data)


@nocodb_tool(
    "update_record",
    "更新指定表中的一条记录。",
    {
        "table_id": {
            "type": "string",
            "description": "表 ID 或名称",
        },
        "record_id": {
            "type": "string",
            "description": "记录 ID（行 ID）",
        },
        "base_id": {
            "type": "string",
            "description": "数据库 ID 或名称（当 table_id 为名称时必填）",
        },
        "fields": {
            "type": "object",
            "description": "要更新的字段键值对",
        },
    },
    is_write=True,
)
def update_record(table_id, record_id, fields, base_id=""):
    resolved_id = _resolve_table_id(table_id, base_id)
    fields["Id"] = record_id
    data = _api_json("PATCH", f"api/v2/tables/{resolved_id}/records", json=fields)
    return _format_json(data)


@nocodb_tool(
    "delete_record",
    "删除指定表中的一条记录。危险操作，不可逆。",
    {
        "table_id": {
            "type": "string",
            "description": "表 ID 或名称",
        },
        "record_id": {
            "type": "string",
            "description": "记录 ID（行 ID）",
        },
        "base_id": {
            "type": "string",
            "description": "数据库 ID 或名称（当 table_id 为名称时必填）",
        },
    },
    is_write=True,
)
def delete_record(table_id, record_id, base_id=""):
    resolved_id = _resolve_table_id(table_id, base_id)
    data = _api_json(
        "DELETE",
        f"api/v2/tables/{resolved_id}/records",
        json=[{"Id": record_id}],
    )
    return f"记录 {record_id} 已删除。"


@nocodb_tool(
    "bulk_delete_records",
    "批量删除指定表中的多条记录。非常危险的操作，不可逆。",
    {
        "table_id": {
            "type": "string",
            "description": "表 ID 或名称",
        },
        "record_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "要删除的记录 ID 列表",
        },
        "base_id": {
            "type": "string",
            "description": "数据库 ID 或名称（当 table_id 为名称时必填）",
        },
    },
    is_write=True,
)
def bulk_delete_records(table_id, record_ids, base_id=""):
    resolved_id = _resolve_table_id(table_id, base_id)
    payload = [{"Id": rid} for rid in record_ids]
    data = _api_json("DELETE", f"api/v2/tables/{resolved_id}/records", json=payload)
    return f"已批量删除 {len(record_ids)} 条记录。"


@nocodb_tool(
    "delete_table",
    "删除整张表及其所有数据。极其危险的操作，不可逆，所有数据将永久丢失。",
    {
        "table_id": {
            "type": "string",
            "description": "表 ID 或名称",
        },
        "base_id": {
            "type": "string",
            "description": "数据库 ID 或名称（当 table_id 为名称时必填）",
        },
    },
    is_write=True,
)
def delete_table(table_id, base_id=""):
    resolved_id = _resolve_table_id(table_id, base_id)
    _api_json("DELETE", f"api/v2/meta/tables/{resolved_id}")
    return f"表 {table_id} 及其所有数据已被永久删除。"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _looks_like_nocodb_id(value):
    """NocoDB IDs are alphanumeric strings (no underscores/hyphens) like 'pvql46tcuoiwvmg'."""
    return len(value) > 10 and value.isalnum()


def _resolve_base_id(base_id_or_name):
    """Resolve base name to ID if needed."""
    if not base_id_or_name:
        raise ToolExecutionError("[错误] base_id 不能为空")
    if _looks_like_nocodb_id(base_id_or_name):
        return base_id_or_name
    # Try to find by name via workspace
    ws_id = _get_default_workspace_id()
    data = _api_json("GET", f"api/v2/meta/workspaces/{ws_id}/bases/", params={"limit": 100})
    bases = data.get("list", []) if isinstance(data, dict) else []
    for b in bases:
        if b.get("title", "").lower() == base_id_or_name.lower():
            return b["id"]
        if b.get("id") == base_id_or_name:
            return b["id"]
    raise ToolExecutionError(f"[错误] 找不到数据库: {base_id_or_name}")


def _resolve_table_id(table_id_or_name, base_id_or_name=""):
    """Resolve table name to ID if needed."""
    if not table_id_or_name:
        raise ToolExecutionError("[错误] table_id 不能为空")
    if _looks_like_nocodb_id(table_id_or_name):
        return table_id_or_name
    # Need base_id to resolve by name
    if not base_id_or_name:
        raise ToolExecutionError("[错误] 当 table_id 为名称时，必须提供 base_id")
    resolved_base = _resolve_base_id(base_id_or_name)
    data = _api_json("GET", f"api/v2/meta/bases/{resolved_base}/tables")
    tables = data.get("list", []) if isinstance(data, dict) else []
    for t in tables:
        if t.get("title", "").lower() == table_id_or_name.lower():
            return t["id"]
        if t.get("id") == table_id_or_name:
            return t["id"]
    raise ToolExecutionError(f"[错误] 在数据库 {base_id_or_name} 中找不到表: {table_id_or_name}")
