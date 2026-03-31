"""
NocoDB API 工具注册。

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
    "base_url": os.environ.get("NOCODB_BASE_URL", "http://localhost:8080").rstrip("/"),
    "token": os.environ.get("NOCODB_API_TOKEN", ""),
    "admin_email": os.environ.get("NOCODB_ADMIN_EMAIL", "admin@example.com"),
    "admin_password": os.environ.get("NOCODB_ADMIN_PASSWORD", "Admin123!"),
}

_jwt_cache = {"token": None}

_REGISTRY = ServiceToolRegistry(service_id="nocodb")


def nocodb_tool(name, description, params, required=None, is_write=False, group="", short_description=""):
    return _REGISTRY.register(
        name=name,
        description=description,
        params=params,
        required=required,
        is_write=is_write,
        group=group,
        short_description=short_description,
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
    if _config["token"]:
        return _config["token"]
    if _jwt_cache["token"]:
        return _jwt_cache["token"]
    _require_requests()
    resp = requests.post(
        f"{_config['base_url']}/api/v1/auth/user/signin",
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
    headers = {"Content-Type": "application/json"}
    if _config["token"]:
        headers["xc-token"] = token
    else:
        headers["xc-auth"] = token
    return headers


def _api(method, path, **kwargs):
    _require_requests()
    url = f"{_config['base_url']}/{path.lstrip('/')}"
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


def _looks_like_nocodb_id(value):
    value = str(value or "")
    return len(value) > 10 and value.isalnum()


def _get_default_workspace_id():
    data = _api_json("GET", "api/v2/meta/workspaces/")
    ws_list = data.get("list", []) if isinstance(data, dict) else []
    if not ws_list:
        raise ToolExecutionError("[错误] 未找到任何 workspace")
    return ws_list[0]["id"]


def _list_workspace_bases(limit=100):
    ws_id = _get_default_workspace_id()
    data = _api_json("GET", f"api/v2/meta/workspaces/{ws_id}/bases/", params={"limit": limit})
    return data.get("list", []) if isinstance(data, dict) else []


def _list_base_tables(base_id):
    data = _api_json("GET", f"api/v2/meta/bases/{base_id}/tables")
    return data.get("list", []) if isinstance(data, dict) else []


def _resolve_base_id(base_id_or_name):
    if not base_id_or_name:
        raise ToolExecutionError("[错误] base_id 不能为空")
    if _looks_like_nocodb_id(base_id_or_name):
        return base_id_or_name
    for base in _list_workspace_bases():
        if base.get("title", "").lower() == str(base_id_or_name).lower():
            return base["id"]
        if base.get("id") == base_id_or_name:
            return base["id"]
    raise ToolExecutionError(f"[错误] 找不到数据库: {base_id_or_name}")


def _resolve_table_id(table_id_or_name, base_id_or_name=""):
    if not table_id_or_name:
        raise ToolExecutionError("[错误] table_id 不能为空")
    if _looks_like_nocodb_id(table_id_or_name):
        return table_id_or_name
    if not base_id_or_name:
        raise ToolExecutionError("[错误] 当 table_id 为名称时，必须提供 base_id")
    resolved_base_id = _resolve_base_id(base_id_or_name)
    for table in _list_base_tables(resolved_base_id):
        if table.get("title", "").lower() == str(table_id_or_name).lower():
            return table["id"]
        if table.get("id") == table_id_or_name:
            return table["id"]
    raise ToolExecutionError(f"[错误] 在数据库 {base_id_or_name} 中找不到表: {table_id_or_name}")


def _get_base_data(base_id_or_name):
    resolved_id = _resolve_base_id(base_id_or_name)
    for base in _list_workspace_bases():
        if base.get("id") == resolved_id:
            return base
    raise ToolExecutionError(f"[错误] 找不到数据库: {base_id_or_name}")


def _get_table_data(table_id_or_name, base_id_or_name=""):
    resolved_id = _resolve_table_id(table_id_or_name, base_id_or_name)
    data = _api_json("GET", f"api/v2/meta/tables/{resolved_id}")
    return data if isinstance(data, dict) else {}


def _normalize_columns(columns):
    results = []
    for column in columns or []:
        results.append(
            {
                "id": column.get("id", ""),
                "title": column.get("title", ""),
                "uidt": column.get("uidt", ""),
                "pk": bool(column.get("pk")),
                "required": bool(column.get("rqd")),
            }
        )
    return results


def _list_records_data(table_id, base_id="", where="", sort="", limit=25, offset=0):
    resolved_id = _resolve_table_id(table_id, base_id)
    params = {"limit": limit, "offset": offset}
    if where:
        params["where"] = where
    if sort:
        params["sort"] = sort
    data = _api_json("GET", f"api/v2/tables/{resolved_id}/records", params=params)
    records = data.get("list", []) if isinstance(data, dict) else []
    page_info = data.get("pageInfo", {}) if isinstance(data, dict) else {}
    return {
        "records": records,
        "totalRows": page_info.get("totalRows", len(records)),
    }


def _build_where_eq(field_name, field_value):
    if not field_name:
        raise ToolExecutionError("[错误] field_name 不能为空")
    value = str(field_value)
    return f"({field_name},eq,{value})"


def _find_records_data(table_id, field_name, field_value, base_id="", limit=10):
    return _list_records_data(
        table_id=table_id,
        base_id=base_id,
        where=_build_where_eq(field_name, field_value),
        limit=limit,
        offset=0,
    )


# ---------------------------------------------------------------------------
# Schema tools
# ---------------------------------------------------------------------------


@nocodb_tool(
    "list_bases",
    "列出 NocoDB 上所有数据库（base）。",
    {
        "per_page": {
            "type": "integer",
            "description": "每页返回数量，默认 20。",
        },
    },
    group="schema",
    short_description="List all bases in the current workspace.",
)
def list_bases(per_page=20):
    results = []
    for base in _list_workspace_bases(limit=per_page):
        results.append(
            {
                "id": base.get("id", ""),
                "title": base.get("title", ""),
                "description": base.get("description", ""),
            }
        )
    return _format_json(results)


@nocodb_tool(
    "get_base",
    "获取指定数据库（base）的详细信息。",
    {
        "base_id": {
            "type": "string",
            "description": "数据库 ID 或名称。",
        },
    },
    required=["base_id"],
    group="schema",
    short_description="Read metadata for one base.",
)
def get_base(base_id):
    base = _get_base_data(base_id)
    return _format_json(
        {
            "id": base.get("id", ""),
            "title": base.get("title", ""),
            "description": base.get("description", ""),
            "workspace_id": base.get("fk_workspace_id", ""),
        }
    )


@nocodb_tool(
    "create_base",
    "创建一个新的数据库（base）。",
    {
        "name": {
            "type": "string",
            "description": "新数据库名称。",
        },
        "description": {
            "type": "string",
            "description": "数据库描述，可选。",
        },
    },
    required=["name"],
    is_write=True,
    group="schema",
    short_description="Create a new base in the default workspace.",
)
def create_base(name, description=""):
    ws_id = _get_default_workspace_id()
    data = _api_json(
        "POST",
        f"api/v2/meta/workspaces/{ws_id}/bases/",
        json={"title": name, "description": description},
    )
    return _format_json(
        {
            "id": data.get("id", ""),
            "title": data.get("title", ""),
            "description": data.get("description", ""),
        }
    )


@nocodb_tool(
    "list_tables",
    "列出指定数据库中的所有表。",
    {
        "base_id": {
            "type": "string",
            "description": "数据库（base）ID 或名称。",
        },
    },
    required=["base_id"],
    group="schema",
    short_description="List all tables inside one base.",
)
def list_tables(base_id):
    resolved_id = _resolve_base_id(base_id)
    results = []
    for table in _list_base_tables(resolved_id):
        results.append(
            {
                "id": table.get("id", ""),
                "title": table.get("title", ""),
                "meta": table.get("meta", {}),
            }
        )
    return _format_json(results)


@nocodb_tool(
    "get_table",
    "获取指定表的详细信息（包括列定义）。",
    {
        "table_id": {
            "type": "string",
            "description": "表 ID 或名称（需同时提供 base_id）。",
        },
        "base_id": {
            "type": "string",
            "description": "数据库 ID 或名称（当 table_id 为名称时必填）。",
        },
    },
    required=["table_id"],
    group="schema",
    short_description="Read table metadata and column definitions.",
)
def get_table(table_id, base_id=""):
    data = _get_table_data(table_id, base_id)
    return _format_json(
        {
            "id": data.get("id", ""),
            "title": data.get("title", ""),
            "columns": _normalize_columns(data.get("columns") or []),
        }
    )


@nocodb_tool(
    "list_columns",
    "列出指定表的列定义。",
    {
        "table_id": {
            "type": "string",
            "description": "表 ID 或名称（需同时提供 base_id）。",
        },
        "base_id": {
            "type": "string",
            "description": "数据库 ID 或名称（当 table_id 为名称时必填）。",
        },
    },
    required=["table_id"],
    group="schema",
    short_description="List columns for a table.",
)
def list_columns(table_id, base_id=""):
    data = _get_table_data(table_id, base_id)
    return _format_json(_normalize_columns(data.get("columns") or []))


@nocodb_tool(
    "create_table",
    "在指定数据库中创建一张新表。",
    {
        "base_id": {
            "type": "string",
            "description": "数据库 ID 或名称。",
        },
        "table_name": {
            "type": "string",
            "description": "新表名称。",
        },
        "columns": {
            "type": "array",
            "description": "列定义数组，如 [{\"name\": \"TaskCode\", \"uidt\": \"SingleLineText\"}]。",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "uidt": {"type": "string"},
                },
                "required": ["name", "uidt"],
            },
        },
    },
    required=["base_id", "table_name", "columns"],
    is_write=True,
    group="schema",
    short_description="Create a new table with column definitions.",
)
def create_table(base_id, table_name, columns):
    resolved_base_id = _resolve_base_id(base_id)
    table_columns = [
        {"column_name": column["name"], "title": column["name"], "uidt": column["uidt"]}
        for column in columns
    ]
    data = _api_json(
        "POST",
        f"api/v2/meta/bases/{resolved_base_id}/tables",
        json={"table_name": table_name, "title": table_name, "columns": table_columns},
    )
    return _format_json(
        {
            "id": data.get("id", ""),
            "title": data.get("title", ""),
            "columns_count": len(columns),
        }
    )


# ---------------------------------------------------------------------------
# Record tools
# ---------------------------------------------------------------------------


@nocodb_tool(
    "list_records",
    "列出指定表中的记录。",
    {
        "table_id": {
            "type": "string",
            "description": "表 ID 或名称。",
        },
        "base_id": {
            "type": "string",
            "description": "数据库 ID 或名称（当 table_id 为名称时必填）。",
        },
        "where": {
            "type": "string",
            "description": "过滤条件，NocoDB where 语法，例如 '(Status,eq,active)'。",
        },
        "sort": {
            "type": "string",
            "description": "排序字段，例如 '-Salary' 或 'FullName'。",
        },
        "limit": {
            "type": "integer",
            "description": "返回数量限制，默认 25。",
        },
        "offset": {
            "type": "integer",
            "description": "偏移量，默认 0。",
        },
    },
    required=["table_id"],
    group="records",
    short_description="List records from a table, optionally filtered.",
)
def list_records(table_id, base_id="", where="", sort="", limit=25, offset=0):
    return _format_json(_list_records_data(table_id, base_id=base_id, where=where, sort=sort, limit=limit, offset=offset))


@nocodb_tool(
    "query_records",
    "按过滤条件查询指定表中的记录。",
    {
        "table_id": {
            "type": "string",
            "description": "表 ID 或名称。",
        },
        "where": {
            "type": "string",
            "description": "过滤条件，NocoDB where 语法，例如 '(ProjectCode,eq,PRJ-DATA)'。",
        },
        "base_id": {
            "type": "string",
            "description": "数据库 ID 或名称（当 table_id 为名称时必填）。",
        },
        "sort": {
            "type": "string",
            "description": "排序字段，例如 '-Priority'。",
        },
        "limit": {
            "type": "integer",
            "description": "返回数量限制，默认 25。",
        },
        "offset": {
            "type": "integer",
            "description": "偏移量，默认 0。",
        },
    },
    required=["table_id", "where"],
    group="views_queries",
    short_description="Query records with an explicit where clause.",
)
def query_records(table_id, where, base_id="", sort="", limit=25, offset=0):
    return _format_json(_list_records_data(table_id, base_id=base_id, where=where, sort=sort, limit=limit, offset=offset))


@nocodb_tool(
    "find_records",
    "按字段值查找匹配的记录。",
    {
        "table_id": {
            "type": "string",
            "description": "表 ID 或名称。",
        },
        "field_name": {
            "type": "string",
            "description": "要匹配的字段名，例如 'ProjectCode'。",
        },
        "field_value": {
            "type": "string",
            "description": "要匹配的字段值。",
        },
        "base_id": {
            "type": "string",
            "description": "数据库 ID 或名称（当 table_id 为名称时必填）。",
        },
        "limit": {
            "type": "integer",
            "description": "返回数量限制，默认 10。",
        },
    },
    required=["table_id", "field_name", "field_value"],
    group="views_queries",
    short_description="Find records by one business field value.",
)
def find_records(table_id, field_name, field_value, base_id="", limit=10):
    return _format_json(_find_records_data(table_id, field_name, field_value, base_id=base_id, limit=limit))


@nocodb_tool(
    "get_record",
    "获取指定表中的单条记录。",
    {
        "table_id": {
            "type": "string",
            "description": "表 ID 或名称。",
        },
        "record_id": {
            "type": "string",
            "description": "记录 ID（行 ID）。",
        },
        "base_id": {
            "type": "string",
            "description": "数据库 ID 或名称（当 table_id 为名称时必填）。",
        },
    },
    required=["table_id", "record_id"],
    group="records",
    short_description="Read one record by row id.",
)
def get_record(table_id, record_id, base_id=""):
    resolved_id = _resolve_table_id(table_id, base_id)
    data = _api_json("GET", f"api/v2/tables/{resolved_id}/records/{record_id}")
    return _format_json(data)


@nocodb_tool(
    "create_record",
    "在指定表中创建一条新记录。",
    {
        "table_id": {
            "type": "string",
            "description": "表 ID 或名称。",
        },
        "base_id": {
            "type": "string",
            "description": "数据库 ID 或名称（当 table_id 为名称时必填）。",
        },
        "fields": {
            "type": "object",
            "description": "记录字段键值对，如 {\"TaskCode\": \"TASK-001\", \"Status\": \"todo\"}。",
        },
    },
    required=["table_id", "fields"],
    is_write=True,
    group="records",
    short_description="Create one record in a table.",
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
            "description": "表 ID 或名称。",
        },
        "record_id": {
            "type": "string",
            "description": "记录 ID（行 ID）。",
        },
        "base_id": {
            "type": "string",
            "description": "数据库 ID 或名称（当 table_id 为名称时必填）。",
        },
        "fields": {
            "type": "object",
            "description": "要更新的字段键值对。",
        },
    },
    required=["table_id", "record_id", "fields"],
    is_write=True,
    group="records",
    short_description="Update one record by row id.",
)
def update_record(table_id, record_id, fields, base_id=""):
    resolved_id = _resolve_table_id(table_id, base_id)
    payload = dict(fields or {})
    payload["Id"] = record_id
    data = _api_json("PATCH", f"api/v2/tables/{resolved_id}/records", json=payload)
    return _format_json(data)


@nocodb_tool(
    "update_record_by_field",
    "按业务字段匹配并更新一条记录。",
    {
        "table_id": {
            "type": "string",
            "description": "表 ID 或名称。",
        },
        "match_field": {
            "type": "string",
            "description": "用于定位记录的字段名，例如 'ProjectCode'。",
        },
        "match_value": {
            "type": "string",
            "description": "用于定位记录的字段值。",
        },
        "fields": {
            "type": "object",
            "description": "要更新的字段键值对。",
        },
        "base_id": {
            "type": "string",
            "description": "数据库 ID 或名称（当 table_id 为名称时必填）。",
        },
        "require_unique": {
            "type": "boolean",
            "description": "是否要求匹配结果唯一，默认 true。",
        },
    },
    required=["table_id", "match_field", "match_value", "fields"],
    is_write=True,
    group="records",
    short_description="Find a record by business key and update it.",
)
def update_record_by_field(table_id, match_field, match_value, fields, base_id="", require_unique=True):
    matched = _find_records_data(table_id, match_field, match_value, base_id=base_id, limit=10)
    records = matched.get("records", [])
    if not records:
        raise ToolExecutionError(f"[错误] 未找到匹配记录: {match_field}={match_value}")
    if require_unique and len(records) != 1:
        raise ToolExecutionError(f"[错误] 匹配到 {len(records)} 条记录，无法唯一更新: {match_field}={match_value}")
    record = records[0]
    record_id = record.get("Id") or record.get("id")
    if record_id in (None, ""):
        raise ToolExecutionError("[错误] 匹配记录缺少 Id 字段，无法更新")
    return update_record(table_id=table_id, record_id=str(record_id), fields=fields, base_id=base_id)


@nocodb_tool(
    "delete_record",
    "删除指定表中的一条记录。危险操作，不可逆。",
    {
        "table_id": {
            "type": "string",
            "description": "表 ID 或名称。",
        },
        "record_id": {
            "type": "string",
            "description": "记录 ID（行 ID）。",
        },
        "base_id": {
            "type": "string",
            "description": "数据库 ID 或名称（当 table_id 为名称时必填）。",
        },
    },
    required=["table_id", "record_id"],
    is_write=True,
    group="records",
    short_description="Delete one record by row id.",
)
def delete_record(table_id, record_id, base_id=""):
    resolved_id = _resolve_table_id(table_id, base_id)
    _api_json("DELETE", f"api/v2/tables/{resolved_id}/records", json=[{"Id": record_id}])
    return f"记录 {record_id} 已删除。"


# ---------------------------------------------------------------------------
# Bulk tools
# ---------------------------------------------------------------------------


@nocodb_tool(
    "bulk_delete_records",
    "批量删除指定表中的多条记录。非常危险的操作，不可逆。",
    {
        "table_id": {
            "type": "string",
            "description": "表 ID 或名称。",
        },
        "record_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "要删除的记录 ID 列表。",
        },
        "base_id": {
            "type": "string",
            "description": "数据库 ID 或名称（当 table_id 为名称时必填）。",
        },
    },
    required=["table_id", "record_ids"],
    is_write=True,
    group="bulk_ops",
    short_description="Delete multiple records at once.",
)
def bulk_delete_records(table_id, record_ids, base_id=""):
    resolved_id = _resolve_table_id(table_id, base_id)
    payload = [{"Id": record_id} for record_id in record_ids]
    _api_json("DELETE", f"api/v2/tables/{resolved_id}/records", json=payload)
    return f"已批量删除 {len(record_ids)} 条记录。"


@nocodb_tool(
    "delete_table",
    "删除整张表及其所有数据。极其危险的操作，不可逆。",
    {
        "table_id": {
            "type": "string",
            "description": "表 ID 或名称。",
        },
        "base_id": {
            "type": "string",
            "description": "数据库 ID 或名称（当 table_id 为名称时必填）。",
        },
    },
    required=["table_id"],
    is_write=True,
    group="bulk_ops",
    short_description="Delete a whole table and all rows.",
)
def delete_table(table_id, base_id=""):
    resolved_id = _resolve_table_id(table_id, base_id)
    _api_json("DELETE", f"api/v2/meta/tables/{resolved_id}")
    return f"表 {table_id} 及其所有数据已被永久删除。"
