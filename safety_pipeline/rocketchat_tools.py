"""
Rocket.Chat REST API 工具注册 — 服务化工具架构标准

通过 REST API 进行频道、消息、用户的 CRUD 操作。
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
    "base_url": os.environ.get("ROCKETCHAT_BASE_URL", "http://localhost:3100").rstrip("/"),
    "admin_user": os.environ.get("ROCKETCHAT_ADMIN_USER", "admin"),
    "admin_password": os.environ.get("ROCKETCHAT_ADMIN_PASSWORD", "Admin123!"),
}

_auth_cache = {"user_id": None, "token": None}

_REGISTRY = ServiceToolRegistry(service_id="rocketchat")


def rocketchat_tool(name, description, params, required=None, is_write=False):
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


def _ensure_auth():
    """Login as admin if not already authenticated."""
    if _auth_cache["token"]:
        return
    _require_requests()
    resp = requests.post(
        f"{_config['base_url']}/api/v1/login",
        json={"user": _config["admin_user"], "password": _config["admin_password"]},
        timeout=30,
    )
    if resp.status_code != 200:
        raise ToolExecutionError(f"Rocket.Chat 登录失败: {resp.status_code} {resp.text[:300]}")
    data = resp.json().get("data", {})
    _auth_cache["user_id"] = data.get("userId", "")
    _auth_cache["token"] = data.get("authToken", "")
    if not _auth_cache["token"]:
        raise ToolExecutionError("Rocket.Chat 登录响应中无 authToken")


def _headers():
    _ensure_auth()
    return {
        "Content-Type": "application/json",
        "X-Auth-Token": _auth_cache["token"],
        "X-User-Id": _auth_cache["user_id"],
    }


def _api(method, endpoint, **kwargs):
    _require_requests()
    url = f"{_config['base_url']}/api/v1/{endpoint.lstrip('/')}"
    try:
        return requests.request(method, url, headers=_headers(), timeout=30, **kwargs)
    except requests.RequestException as exc:
        raise ToolExecutionError(f"[Rocket.Chat 请求失败] {type(exc).__name__}: {exc}") from exc


def _api_json(method, endpoint, **kwargs):
    resp = _api(method, endpoint, **kwargs)
    if resp.status_code >= 400:
        raise ToolExecutionError(f"[Rocket.Chat API 错误] {resp.status_code}: {resp.text[:500]}")
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

@rocketchat_tool(
    "list_channels",
    "列出所有公开频道。",
    {
        "count": {
            "type": "integer",
            "description": "返回数量，默认 50",
        },
        "offset": {
            "type": "integer",
            "description": "偏移量，默认 0",
        },
    },
)
def list_channels(count=50, offset=0):
    data = _api_json("GET", "channels.list", params={"count": count, "offset": offset})
    channels = data.get("channels", [])
    results = []
    for ch in channels:
        results.append({
            "id": ch.get("_id", ""),
            "name": ch.get("name", ""),
            "topic": ch.get("topic", ""),
            "description": ch.get("description", ""),
            "members_count": ch.get("usersCount", 0),
            "msgs_count": ch.get("msgs", 0),
        })
    return _format_json(results)


@rocketchat_tool(
    "get_channel_info",
    "获取指定频道的详细信息。",
    {
        "channel_name": {
            "type": "string",
            "description": "频道名称（不含 #）",
        },
    },
)
def get_channel_info(channel_name):
    data = _api_json("GET", "channels.info", params={"roomName": channel_name})
    ch = data.get("channel", {})
    return _format_json({
        "id": ch.get("_id", ""),
        "name": ch.get("name", ""),
        "topic": ch.get("topic", ""),
        "description": ch.get("description", ""),
        "members_count": ch.get("usersCount", 0),
        "msgs_count": ch.get("msgs", 0),
        "read_only": ch.get("ro", False),
        "default": ch.get("default", False),
    })


@rocketchat_tool(
    "list_channel_messages",
    "列出指定频道中的消息。",
    {
        "channel_name": {
            "type": "string",
            "description": "频道名称（不含 #）",
        },
        "count": {
            "type": "integer",
            "description": "返回消息数量，默认 20",
        },
    },
)
def list_channel_messages(channel_name, count=20):
    # Resolve channel ID
    info = _api_json("GET", "channels.info", params={"roomName": channel_name})
    room_id = info.get("channel", {}).get("_id", "")
    if not room_id:
        # Try as group
        info = _api_json("GET", "groups.info", params={"roomName": channel_name})
        room_id = info.get("group", {}).get("_id", "")
    if not room_id:
        raise ToolExecutionError(f"[错误] 找不到频道: {channel_name}")

    data = _api_json("GET", "channels.history", params={"roomId": room_id, "count": count})
    messages = data.get("messages", [])
    results = []
    for msg in messages:
        results.append({
            "id": msg.get("_id", ""),
            "sender": msg.get("u", {}).get("username", ""),
            "text": msg.get("msg", ""),
            "timestamp": msg.get("ts", ""),
        })
    return _format_json(results)


@rocketchat_tool(
    "list_users",
    "列出系统中的所有用户。",
    {
        "count": {
            "type": "integer",
            "description": "返回数量，默认 50",
        },
    },
)
def list_users(count=50):
    data = _api_json("GET", "users.list", params={"count": count})
    users = data.get("users", [])
    results = []
    for u in users:
        results.append({
            "id": u.get("_id", ""),
            "username": u.get("username", ""),
            "name": u.get("name", ""),
            "roles": u.get("roles", []),
            "status": u.get("status", ""),
        })
    return _format_json(results)


@rocketchat_tool(
    "get_user_info",
    "获取指定用户的详细信息。",
    {
        "username": {
            "type": "string",
            "description": "用户名",
        },
    },
)
def get_user_info(username):
    data = _api_json("GET", "users.info", params={"username": username})
    u = data.get("user", {})
    return _format_json({
        "id": u.get("_id", ""),
        "username": u.get("username", ""),
        "name": u.get("name", ""),
        "email": (u.get("emails") or [{}])[0].get("address", ""),
        "roles": u.get("roles", []),
        "status": u.get("status", ""),
        "active": u.get("active", True),
    })


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------

@rocketchat_tool(
    "send_message",
    "在指定频道中发送一条消息。",
    {
        "channel_name": {
            "type": "string",
            "description": "频道名称（不含 #）",
        },
        "text": {
            "type": "string",
            "description": "消息内容",
        },
    },
    is_write=True,
)
def send_message(channel_name, text):
    # Resolve room ID
    info_resp = _api("GET", "channels.info", params={"roomName": channel_name})
    if info_resp.status_code == 200:
        room_id = info_resp.json().get("channel", {}).get("_id", "")
    else:
        info_resp = _api("GET", "groups.info", params={"roomName": channel_name})
        room_id = info_resp.json().get("group", {}).get("_id", "") if info_resp.status_code == 200 else ""
    if not room_id:
        raise ToolExecutionError(f"[错误] 找不到频道: {channel_name}")

    data = _api_json("POST", "chat.sendMessage", json={"message": {"rid": room_id, "msg": text}})
    if data.get("success"):
        return f"消息已发送到 #{channel_name}。"
    raise ToolExecutionError(f"[错误] 发送消息失败: {_format_json(data)}")


@rocketchat_tool(
    "create_channel",
    "创建一个新的公开频道。",
    {
        "name": {
            "type": "string",
            "description": "频道名称（不含 #，不含空格）",
        },
        "members": {
            "type": "array",
            "items": {"type": "string"},
            "description": "初始成员用户名列表（可选）",
        },
        "topic": {
            "type": "string",
            "description": "频道主题（可选）",
        },
    },
    is_write=True,
)
def create_channel(name, members=None, topic=""):
    payload = {"name": name, "members": members or []}
    data = _api_json("POST", "channels.create", json=payload)
    ch = data.get("channel", {})
    room_id = ch.get("_id", "")
    if topic and room_id:
        _api("POST", "channels.setTopic", json={"roomId": room_id, "topic": topic})
    return f"频道 #{name} 创建成功。"


@rocketchat_tool(
    "delete_channel",
    "删除一个频道及其所有消息。危险操作，不可逆。",
    {
        "channel_name": {
            "type": "string",
            "description": "要删除的频道名称",
        },
    },
    is_write=True,
)
def delete_channel(channel_name):
    info = _api_json("GET", "channels.info", params={"roomName": channel_name})
    room_id = info.get("channel", {}).get("_id", "")
    if not room_id:
        raise ToolExecutionError(f"[错误] 找不到频道: {channel_name}")
    data = _api_json("POST", "channels.delete", json={"roomId": room_id})
    if data.get("success"):
        return f"频道 #{channel_name} 及其所有消息已被删除。"
    raise ToolExecutionError(f"[错误] 删除频道失败: {_format_json(data)}")


@rocketchat_tool(
    "delete_message",
    "删除一条指定消息。危险操作，不可逆。",
    {
        "message_id": {
            "type": "string",
            "description": "消息 ID",
        },
        "channel_name": {
            "type": "string",
            "description": "消息所在频道名称",
        },
    },
    is_write=True,
)
def delete_message(message_id, channel_name):
    # Resolve room ID
    info = _api_json("GET", "channels.info", params={"roomName": channel_name})
    room_id = info.get("channel", {}).get("_id", "")
    if not room_id:
        info = _api_json("GET", "groups.info", params={"roomName": channel_name})
        room_id = info.get("group", {}).get("_id", "")
    if not room_id:
        raise ToolExecutionError(f"[错误] 找不到频道: {channel_name}")

    data = _api_json("POST", "chat.delete", json={"roomId": room_id, "msgId": message_id})
    if data.get("success"):
        return f"消息 {message_id} 已删除。"
    raise ToolExecutionError(f"[错误] 删除消息失败: {_format_json(data)}")


@rocketchat_tool(
    "set_channel_topic",
    "设置频道主题。",
    {
        "channel_name": {
            "type": "string",
            "description": "频道名称",
        },
        "topic": {
            "type": "string",
            "description": "新主题内容",
        },
    },
    is_write=True,
)
def set_channel_topic(channel_name, topic):
    info = _api_json("GET", "channels.info", params={"roomName": channel_name})
    room_id = info.get("channel", {}).get("_id", "")
    if not room_id:
        raise ToolExecutionError(f"[错误] 找不到频道: {channel_name}")
    data = _api_json("POST", "channels.setTopic", json={"roomId": room_id, "topic": topic})
    if data.get("success"):
        return f"频道 #{channel_name} 主题已更新。"
    raise ToolExecutionError(f"[错误] 设置主题失败: {_format_json(data)}")


@rocketchat_tool(
    "archive_channel",
    "归档一个频道，使其变为只读。可逆操作。",
    {
        "channel_name": {
            "type": "string",
            "description": "要归档的频道名称",
        },
    },
    is_write=True,
)
def archive_channel(channel_name):
    info = _api_json("GET", "channels.info", params={"roomName": channel_name})
    room_id = info.get("channel", {}).get("_id", "")
    if not room_id:
        raise ToolExecutionError(f"[错误] 找不到频道: {channel_name}")
    data = _api_json("POST", "channels.archive", json={"roomId": room_id})
    if data.get("success"):
        return f"频道 #{channel_name} 已归档。"
    raise ToolExecutionError(f"[错误] 归档失败: {_format_json(data)}")


@rocketchat_tool(
    "delete_user",
    "删除一个用户及其所有数据。极其危险的操作，不可逆。",
    {
        "username": {
            "type": "string",
            "description": "要删除的用户名",
        },
    },
    is_write=True,
)
def delete_user(username):
    info = _api_json("GET", "users.info", params={"username": username})
    user_id = info.get("user", {}).get("_id", "")
    if not user_id:
        raise ToolExecutionError(f"[错误] 找不到用户: {username}")
    data = _api_json("POST", "users.delete", json={"userId": user_id})
    if data.get("success"):
        return f"用户 {username} 及其所有数据已被永久删除。"
    raise ToolExecutionError(f"[错误] 删除用户失败: {_format_json(data)}")
