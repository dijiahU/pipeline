"""
Rocket.Chat REST API 工具注册。

通过 REST API 进行公开频道、私有频道、消息、私聊、用户与 integration 的读写操作。
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


def rocketchat_tool(name, description, params, required=None, is_write=False, group="", short_description=""):
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
        raise ToolExecutionError("requests 库未安装。pip install requests")


def _ensure_auth():
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
        return {"success": False, "raw": resp.text[:1000]}


def _format_json(data):
    if isinstance(data, str):
        return data
    return json.dumps(data, ensure_ascii=False, indent=2)


def _normalize_room(room, room_type):
    return {
        "id": room.get("_id", ""),
        "name": room.get("name", room.get("fname", "")),
        "type": room_type,
        "topic": room.get("topic", ""),
        "description": room.get("description", ""),
        "members_count": room.get("usersCount", 0),
        "msgs_count": room.get("msgs", 0),
        "read_only": room.get("ro", False),
        "archived": room.get("_hidden", False) or room.get("archived", False),
        "default": room.get("default", False),
    }


def _normalize_user(user):
    return {
        "id": user.get("_id", ""),
        "username": user.get("username", ""),
        "name": user.get("name", ""),
        "email": (user.get("emails") or [{}])[0].get("address", ""),
        "roles": user.get("roles", []),
        "status": user.get("status", ""),
        "active": user.get("active", True),
    }


def _normalize_message(message):
    return {
        "id": message.get("_id", ""),
        "room_id": message.get("rid", ""),
        "text": message.get("msg", ""),
        "sender": message.get("u", {}).get("username", ""),
        "timestamp": message.get("ts", ""),
        "thread_id": message.get("tmid", ""),
        "show_in_room": message.get("tshow", False),
        "pinned": bool(message.get("pinned")),
        "replies": message.get("tcount", 0),
    }


def _normalize_integration(integration):
    urls = integration.get("urls") or []
    if integration.get("target_url") and not urls:
        urls = [integration.get("target_url")]
    return {
        "id": integration.get("_id", integration.get("integrationId", "")),
        "name": integration.get("name", ""),
        "type": integration.get("type", ""),
        "enabled": integration.get("enabled", True),
        "channel": integration.get("channel", integration.get("channelName", "")),
        "username": integration.get("username", ""),
        "urls": urls,
        "event": integration.get("event", ""),
    }


def _public_room_info(channel_name):
    data = _api_json("GET", "channels.info", params={"roomName": channel_name})
    channel = data.get("channel") or {}
    if not channel.get("_id"):
        raise ToolExecutionError(f"[错误] 找不到公开频道: {channel_name}")
    return channel


def _private_room_info(room_name):
    data = _api_json("GET", "groups.info", params={"roomName": room_name})
    group = data.get("group") or {}
    if not group.get("_id"):
        raise ToolExecutionError(f"[错误] 找不到私有频道: {room_name}")
    return group


def _room_info(room_name, room_kind="any"):
    if room_kind == "public":
        return _public_room_info(room_name), "public"
    if room_kind == "private":
        return _private_room_info(room_name), "private"

    try:
        return _public_room_info(room_name), "public"
    except ToolExecutionError:
        return _private_room_info(room_name), "private"


def _room_history(room_name, count=20, offset=0, room_kind="any"):
    room, resolved_kind = _room_info(room_name, room_kind=room_kind)
    endpoint = "channels.history" if resolved_kind == "public" else "groups.history"
    data = _api_json(
        "GET",
        endpoint,
        params={"roomId": room.get("_id", ""), "count": count, "offset": offset},
    )
    return room, resolved_kind, data.get("messages", [])


def _user_info(username):
    data = _api_json("GET", "users.info", params={"username": username})
    user = data.get("user") or {}
    if not user.get("_id"):
        raise ToolExecutionError(f"[错误] 找不到用户: {username}")
    return user


def _user_id(username):
    return _user_info(username).get("_id", "")


def _message_info(message_id):
    data = _api_json("GET", "chat.getMessage", params={"msgId": message_id})
    message = data.get("message") or {}
    if not message.get("_id"):
        raise ToolExecutionError(f"[错误] 找不到消息: {message_id}")
    return message


def _find_integration(name="", integration_type="", integration_id=""):
    if integration_id:
        data = _api_json("GET", "integrations.get", params={"integrationId": integration_id})
        integration = data.get("integration") or {}
        if not integration.get("_id"):
            raise ToolExecutionError(f"[错误] 找不到 integration: {integration_id}")
        return integration

    params = {"count": 100, "offset": 0}
    if name:
        params["name"] = name
    data = _api_json("GET", "integrations.list", params=params)
    integrations = data.get("integrations", []) or []
    for integration in integrations:
        if name and integration.get("name") != name:
            continue
        if integration_type and integration.get("type") != integration_type:
            continue
        return integration
    raise ToolExecutionError(f"[错误] 找不到 integration: {name or integration_id}")


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------

@rocketchat_tool(
    "list_channels",
    "列出公开频道。",
    {
        "count": {"type": "integer", "description": "返回数量，默认 50"},
        "offset": {"type": "integer", "description": "偏移量，默认 0"},
    },
    group="channels",
    short_description="List public channels with topic and description.",
)
def list_channels(count=50, offset=0):
    data = _api_json("GET", "channels.list", params={"count": count, "offset": offset})
    return _format_json([_normalize_room(item, "public") for item in data.get("channels", [])])


@rocketchat_tool(
    "get_channel_info",
    "获取公开频道的详细信息。",
    {
        "channel_name": {"type": "string", "description": "公开频道名称（不含 #）"},
    },
    group="channels",
    short_description="Read a public channel's metadata and current settings.",
)
def get_channel_info(channel_name):
    return _format_json(_normalize_room(_public_room_info(channel_name), "public"))


@rocketchat_tool(
    "list_channel_members",
    "列出公开频道成员。",
    {
        "channel_name": {"type": "string", "description": "公开频道名称（不含 #）"},
        "count": {"type": "integer", "description": "返回数量，默认 100"},
        "offset": {"type": "integer", "description": "偏移量，默认 0"},
    },
    group="channels",
    short_description="List members of a public channel.",
)
def list_channel_members(channel_name, count=100, offset=0):
    channel = _public_room_info(channel_name)
    data = _api_json(
        "GET",
        "channels.members",
        params={"roomId": channel.get("_id", ""), "count": count, "offset": offset},
    )
    return _format_json([_normalize_user(user) for user in data.get("members", [])])


@rocketchat_tool(
    "list_private_channels",
    "列出私有频道。",
    {
        "count": {"type": "integer", "description": "返回数量，默认 50"},
        "offset": {"type": "integer", "description": "偏移量，默认 0"},
    },
    group="private_channels",
    short_description="List private channels visible to the admin user.",
)
def list_private_channels(count=50, offset=0):
    data = _api_json("GET", "groups.listAll", params={"count": count, "offset": offset})
    return _format_json([_normalize_room(item, "private") for item in data.get("groups", [])])


@rocketchat_tool(
    "get_private_channel_info",
    "获取私有频道的详细信息。",
    {
        "room_name": {"type": "string", "description": "私有频道名称（不含 #）"},
    },
    group="private_channels",
    short_description="Read a private channel's metadata and settings.",
)
def get_private_channel_info(room_name):
    return _format_json(_normalize_room(_private_room_info(room_name), "private"))


@rocketchat_tool(
    "list_private_channel_members",
    "列出私有频道成员。",
    {
        "room_name": {"type": "string", "description": "私有频道名称（不含 #）"},
        "count": {"type": "integer", "description": "返回数量，默认 100"},
        "offset": {"type": "integer", "description": "偏移量，默认 0"},
    },
    group="private_channels",
    short_description="List members of a private channel.",
)
def list_private_channel_members(room_name, count=100, offset=0):
    group = _private_room_info(room_name)
    data = _api_json(
        "GET",
        "groups.members",
        params={"roomId": group.get("_id", ""), "count": count, "offset": offset},
    )
    return _format_json([_normalize_user(user) for user in data.get("members", [])])


@rocketchat_tool(
    "list_channel_messages",
    "列出公开频道或私有频道中的消息。",
    {
        "room_name": {"type": "string", "description": "公开频道或私有频道名称（不含 #）"},
        "count": {"type": "integer", "description": "返回消息数量，默认 20"},
        "offset": {"type": "integer", "description": "偏移量，默认 0"},
    },
    group="messages",
    short_description="Read recent messages from a room.",
)
def list_channel_messages(room_name, count=20, offset=0):
    _, _, messages = _room_history(room_name, count=count, offset=offset)
    return _format_json([_normalize_message(msg) for msg in messages])


@rocketchat_tool(
    "get_message",
    "读取单条消息详情。",
    {
        "message_id": {"type": "string", "description": "消息 ID"},
    },
    group="messages",
    short_description="Inspect one message by ID.",
)
def get_message(message_id):
    return _format_json(_normalize_message(_message_info(message_id)))


@rocketchat_tool(
    "list_thread_messages",
    "读取线程中的回复消息。",
    {
        "message_id": {"type": "string", "description": "线程根消息 ID"},
        "count": {"type": "integer", "description": "返回消息数量，默认 20"},
    },
    group="messages",
    short_description="Read replies under a thread root message.",
)
def list_thread_messages(message_id, count=20):
    data = _api_json(
        "GET",
        "chat.getThreadMessages",
        params={"tmid": message_id, "count": count},
    )
    return _format_json([_normalize_message(msg) for msg in data.get("messages", [])])


@rocketchat_tool(
    "list_direct_messages",
    "列出当前管理员可见的私聊会话。",
    {
        "count": {"type": "integer", "description": "返回数量，默认 50"},
        "offset": {"type": "integer", "description": "偏移量，默认 0"},
    },
    group="direct_messages",
    short_description="List DM rooms visible to the current admin user.",
)
def list_direct_messages(count=50, offset=0):
    data = _api_json("GET", "dm.list", params={"count": count, "offset": offset})
    results = []
    for room in data.get("ims", []):
        results.append({
            "id": room.get("_id", ""),
            "name": room.get("name", room.get("fname", "")),
            "usernames": room.get("usernames", []),
            "msgs_count": room.get("msgs", 0),
        })
    return _format_json(results)


@rocketchat_tool(
    "list_direct_message_messages",
    "读取指定私聊会话中的消息。",
    {
        "username": {"type": "string", "description": "对方用户名"},
        "count": {"type": "integer", "description": "返回消息数量，默认 20"},
        "offset": {"type": "integer", "description": "偏移量，默认 0"},
    },
    group="direct_messages",
    short_description="Read messages from the DM room with a given user.",
)
def list_direct_message_messages(username, count=20, offset=0):
    room = _api_json("POST", "dm.create", json={"username": username}).get("room", {})
    room_id = room.get("_id", "")
    if not room_id:
        raise ToolExecutionError(f"[错误] 无法创建或定位与 {username} 的私聊")
    data = _api_json(
        "GET",
        "dm.messages",
        params={"roomId": room_id, "count": count, "offset": offset},
    )
    return _format_json([_normalize_message(msg) for msg in data.get("messages", [])])


@rocketchat_tool(
    "list_users",
    "列出系统中的用户。",
    {
        "count": {"type": "integer", "description": "返回数量，默认 50"},
        "offset": {"type": "integer", "description": "偏移量，默认 0"},
    },
    group="users",
    short_description="List workspace users and their current status.",
)
def list_users(count=50, offset=0):
    data = _api_json("GET", "users.list", params={"count": count, "offset": offset})
    return _format_json([_normalize_user(user) for user in data.get("users", [])])


@rocketchat_tool(
    "get_user_info",
    "获取指定用户的详细信息。",
    {
        "username": {"type": "string", "description": "用户名"},
    },
    group="users",
    short_description="Read a user's profile, roles, and status.",
)
def get_user_info(username):
    return _format_json(_normalize_user(_user_info(username)))


@rocketchat_tool(
    "list_integrations",
    "列出当前工作区中的 integration。",
    {
        "count": {"type": "integer", "description": "返回数量，默认 50"},
        "offset": {"type": "integer", "description": "偏移量，默认 0"},
        "name_filter": {"type": "string", "description": "按 integration 名称过滤（可选）"},
    },
    group="integrations",
    short_description="List incoming and outgoing integrations.",
)
def list_integrations(count=50, offset=0, name_filter=""):
    params = {"count": count, "offset": offset}
    if name_filter:
        params["name"] = name_filter
    data = _api_json("GET", "integrations.list", params=params)
    return _format_json([_normalize_integration(item) for item in data.get("integrations", [])])


@rocketchat_tool(
    "get_integration",
    "读取单个 integration 的详细信息。",
    {
        "name": {"type": "string", "description": "integration 名称"},
        "integration_type": {"type": "string", "description": "integration 类型，例如 webhook-incoming 或 webhook-outgoing"},
    },
    group="integrations",
    short_description="Inspect one integration by name and type.",
)
def get_integration(name, integration_type=""):
    return _format_json(_normalize_integration(_find_integration(name=name, integration_type=integration_type)))


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------

@rocketchat_tool(
    "send_message",
    "向公开频道或私有频道发送消息。",
    {
        "room_name": {"type": "string", "description": "公开频道或私有频道名称（不含 #）"},
        "text": {"type": "string", "description": "消息内容"},
    },
    is_write=True,
    group="messages",
    short_description="Post a message into a room.",
)
def send_message(room_name, text):
    room, _, _ = _room_history(room_name, count=1, offset=0)
    data = _api_json("POST", "chat.sendMessage", json={"message": {"rid": room.get("_id", ""), "msg": text}})
    if data.get("success"):
        return _format_json(_normalize_message(data.get("message", {})))
    raise ToolExecutionError(f"[错误] 发送消息失败: {_format_json(data)}")


@rocketchat_tool(
    "send_thread_reply",
    "向现有线程回复消息。",
    {
        "message_id": {"type": "string", "description": "线程根消息 ID"},
        "text": {"type": "string", "description": "回复内容"},
        "show_in_room": {"type": "boolean", "description": "是否同时在主房间显示回复，默认 false"},
    },
    is_write=True,
    group="messages",
    short_description="Reply inside an existing thread.",
)
def send_thread_reply(message_id, text, show_in_room=False):
    root_message = _message_info(message_id)
    data = _api_json(
        "POST",
        "chat.sendMessage",
        json={
            "message": {
                "rid": root_message.get("rid", ""),
                "msg": text,
                "tmid": message_id,
                "tshow": bool(show_in_room),
            }
        },
    )
    if data.get("success"):
        return _format_json(_normalize_message(data.get("message", {})))
    raise ToolExecutionError(f"[错误] 发送线程回复失败: {_format_json(data)}")


@rocketchat_tool(
    "pin_message",
    "置顶一条消息。",
    {
        "message_id": {"type": "string", "description": "消息 ID"},
    },
    is_write=True,
    group="messages",
    short_description="Pin an existing message.",
)
def pin_message(message_id):
    data = _api_json("POST", "chat.pinMessage", json={"messageId": message_id})
    if data.get("success"):
        return f"消息 {message_id} 已置顶。"
    raise ToolExecutionError(f"[错误] 置顶消息失败: {_format_json(data)}")


@rocketchat_tool(
    "unpin_message",
    "取消置顶一条消息。",
    {
        "message_id": {"type": "string", "description": "消息 ID"},
    },
    is_write=True,
    group="messages",
    short_description="Remove pin from a message.",
)
def unpin_message(message_id):
    data = _api_json("POST", "chat.unPinMessage", json={"messageId": message_id})
    if data.get("success"):
        return f"消息 {message_id} 已取消置顶。"
    raise ToolExecutionError(f"[错误] 取消置顶失败: {_format_json(data)}")


@rocketchat_tool(
    "delete_message",
    "删除一条消息。危险操作，不可逆。",
    {
        "message_id": {"type": "string", "description": "消息 ID"},
    },
    is_write=True,
    group="messages",
    short_description="Delete one message permanently.",
)
def delete_message(message_id):
    message = _message_info(message_id)
    data = _api_json("POST", "chat.delete", json={"roomId": message.get("rid", ""), "msgId": message_id})
    if data.get("success"):
        return f"消息 {message_id} 已删除。"
    raise ToolExecutionError(f"[错误] 删除消息失败: {_format_json(data)}")


@rocketchat_tool(
    "create_channel",
    "创建新的公开频道。",
    {
        "name": {"type": "string", "description": "频道名称（不含 #，不含空格）"},
        "members": {"type": "array", "items": {"type": "string"}, "description": "初始成员用户名列表（可选）"},
        "topic": {"type": "string", "description": "频道主题（可选）"},
        "description": {"type": "string", "description": "频道描述（可选）"},
    },
    is_write=True,
    group="channels",
    short_description="Create a new public channel.",
)
def create_channel(name, members=None, topic="", description=""):
    data = _api_json("POST", "channels.create", json={"name": name, "members": members or []})
    channel = data.get("channel") or {}
    room_id = channel.get("_id", "")
    if not room_id:
        raise ToolExecutionError(f"[错误] 创建公开频道失败: {_format_json(data)}")
    if topic:
        _api_json("POST", "channels.setTopic", json={"roomId": room_id, "topic": topic})
    if description:
        _api_json("POST", "channels.setDescription", json={"roomId": room_id, "description": description})
    return f"公开频道 #{name} 创建成功。"


@rocketchat_tool(
    "invite_user_to_channel",
    "邀请用户加入公开频道。",
    {
        "channel_name": {"type": "string", "description": "公开频道名称（不含 #）"},
        "username": {"type": "string", "description": "要邀请的用户名"},
    },
    is_write=True,
    group="channels",
    short_description="Invite a user into a public channel.",
)
def invite_user_to_channel(channel_name, username):
    channel = _public_room_info(channel_name)
    data = _api_json(
        "POST",
        "channels.invite",
        json={"roomId": channel.get("_id", ""), "userId": _user_id(username)},
    )
    if data.get("success"):
        return f"已邀请 {username} 加入 #{channel_name}。"
    raise ToolExecutionError(f"[错误] 邀请用户加入公开频道失败: {_format_json(data)}")


@rocketchat_tool(
    "remove_user_from_channel",
    "将用户移出公开频道。",
    {
        "channel_name": {"type": "string", "description": "公开频道名称（不含 #）"},
        "username": {"type": "string", "description": "要移出的用户名"},
    },
    is_write=True,
    group="moderation",
    short_description="Remove a user from a public channel.",
)
def remove_user_from_channel(channel_name, username):
    channel = _public_room_info(channel_name)
    data = _api_json(
        "POST",
        "channels.kick",
        json={"roomId": channel.get("_id", ""), "userId": _user_id(username)},
    )
    if data.get("success"):
        return f"已将 {username} 移出 #{channel_name}。"
    raise ToolExecutionError(f"[错误] 移出公开频道成员失败: {_format_json(data)}")


@rocketchat_tool(
    "set_channel_topic",
    "设置公开频道主题。",
    {
        "channel_name": {"type": "string", "description": "公开频道名称（不含 #）"},
        "topic": {"type": "string", "description": "新主题内容"},
    },
    is_write=True,
    group="channels",
    short_description="Update a public channel topic.",
)
def set_channel_topic(channel_name, topic):
    channel = _public_room_info(channel_name)
    data = _api_json("POST", "channels.setTopic", json={"roomId": channel.get("_id", ""), "topic": topic})
    if data.get("success"):
        return f"频道 #{channel_name} 主题已更新。"
    raise ToolExecutionError(f"[错误] 设置公开频道主题失败: {_format_json(data)}")


@rocketchat_tool(
    "set_channel_description",
    "设置公开频道描述。",
    {
        "channel_name": {"type": "string", "description": "公开频道名称（不含 #）"},
        "description": {"type": "string", "description": "新描述内容"},
    },
    is_write=True,
    group="channels",
    short_description="Update a public channel description.",
)
def set_channel_description(channel_name, description):
    channel = _public_room_info(channel_name)
    data = _api_json(
        "POST",
        "channels.setDescription",
        json={"roomId": channel.get("_id", ""), "description": description},
    )
    if data.get("success"):
        return f"频道 #{channel_name} 描述已更新。"
    raise ToolExecutionError(f"[错误] 设置公开频道描述失败: {_format_json(data)}")


@rocketchat_tool(
    "archive_channel",
    "归档公开频道，使其变为只读。",
    {
        "channel_name": {"type": "string", "description": "公开频道名称（不含 #）"},
    },
    is_write=True,
    group="moderation",
    short_description="Archive a public channel.",
)
def archive_channel(channel_name):
    channel = _public_room_info(channel_name)
    data = _api_json("POST", "channels.archive", json={"roomId": channel.get("_id", "")})
    if data.get("success"):
        return f"频道 #{channel_name} 已归档。"
    raise ToolExecutionError(f"[错误] 归档公开频道失败: {_format_json(data)}")


@rocketchat_tool(
    "delete_channel",
    "删除公开频道及其所有消息。危险操作，不可逆。",
    {
        "channel_name": {"type": "string", "description": "要删除的公开频道名称（不含 #）"},
    },
    is_write=True,
    group="moderation",
    short_description="Delete a public channel permanently.",
)
def delete_channel(channel_name):
    channel = _public_room_info(channel_name)
    data = _api_json("POST", "rooms.delete", json={"roomId": channel.get("_id", "")})
    if data.get("success"):
        return f"公开频道 #{channel_name} 已删除。"
    raise ToolExecutionError(f"[错误] 删除公开频道失败: {_format_json(data)}")


@rocketchat_tool(
    "create_private_channel",
    "创建新的私有频道。",
    {
        "name": {"type": "string", "description": "私有频道名称（不含 #，不含空格）"},
        "members": {"type": "array", "items": {"type": "string"}, "description": "初始成员用户名列表（可选）"},
        "topic": {"type": "string", "description": "频道主题（可选）"},
        "description": {"type": "string", "description": "频道描述（可选）"},
    },
    is_write=True,
    group="private_channels",
    short_description="Create a new private channel.",
)
def create_private_channel(name, members=None, topic="", description=""):
    data = _api_json("POST", "groups.create", json={"name": name, "members": members or []})
    group = data.get("group") or {}
    room_id = group.get("_id", "")
    if not room_id:
        raise ToolExecutionError(f"[错误] 创建私有频道失败: {_format_json(data)}")
    if topic:
        _api_json("POST", "groups.setTopic", json={"roomId": room_id, "topic": topic})
    if description:
        _api_json("POST", "groups.setDescription", json={"roomId": room_id, "description": description})
    return f"私有频道 #{name} 创建成功。"


@rocketchat_tool(
    "invite_user_to_private_channel",
    "邀请用户加入私有频道。",
    {
        "room_name": {"type": "string", "description": "私有频道名称（不含 #）"},
        "username": {"type": "string", "description": "要邀请的用户名"},
    },
    is_write=True,
    group="private_channels",
    short_description="Invite a user into a private channel.",
)
def invite_user_to_private_channel(room_name, username):
    group = _private_room_info(room_name)
    data = _api_json(
        "POST",
        "groups.invite",
        json={"roomId": group.get("_id", ""), "userId": _user_id(username)},
    )
    if data.get("success"):
        return f"已邀请 {username} 加入私有频道 #{room_name}。"
    raise ToolExecutionError(f"[错误] 邀请用户加入私有频道失败: {_format_json(data)}")


@rocketchat_tool(
    "remove_user_from_private_channel",
    "将用户移出私有频道。",
    {
        "room_name": {"type": "string", "description": "私有频道名称（不含 #）"},
        "username": {"type": "string", "description": "要移出的用户名"},
    },
    is_write=True,
    group="moderation",
    short_description="Remove a user from a private channel.",
)
def remove_user_from_private_channel(room_name, username):
    group = _private_room_info(room_name)
    data = _api_json(
        "POST",
        "groups.kick",
        json={"roomId": group.get("_id", ""), "userId": _user_id(username)},
    )
    if data.get("success"):
        return f"已将 {username} 移出私有频道 #{room_name}。"
    raise ToolExecutionError(f"[错误] 移出私有频道成员失败: {_format_json(data)}")


@rocketchat_tool(
    "set_private_channel_topic",
    "设置私有频道主题。",
    {
        "room_name": {"type": "string", "description": "私有频道名称（不含 #）"},
        "topic": {"type": "string", "description": "新主题内容"},
    },
    is_write=True,
    group="private_channels",
    short_description="Update a private channel topic.",
)
def set_private_channel_topic(room_name, topic):
    group = _private_room_info(room_name)
    data = _api_json("POST", "groups.setTopic", json={"roomId": group.get("_id", ""), "topic": topic})
    if data.get("success"):
        return f"私有频道 #{room_name} 主题已更新。"
    raise ToolExecutionError(f"[错误] 设置私有频道主题失败: {_format_json(data)}")


@rocketchat_tool(
    "set_private_channel_description",
    "设置私有频道描述。",
    {
        "room_name": {"type": "string", "description": "私有频道名称（不含 #）"},
        "description": {"type": "string", "description": "新描述内容"},
    },
    is_write=True,
    group="private_channels",
    short_description="Update a private channel description.",
)
def set_private_channel_description(room_name, description):
    group = _private_room_info(room_name)
    data = _api_json(
        "POST",
        "groups.setDescription",
        json={"roomId": group.get("_id", ""), "description": description},
    )
    if data.get("success"):
        return f"私有频道 #{room_name} 描述已更新。"
    raise ToolExecutionError(f"[错误] 设置私有频道描述失败: {_format_json(data)}")


@rocketchat_tool(
    "archive_private_channel",
    "归档私有频道，使其变为只读。",
    {
        "room_name": {"type": "string", "description": "私有频道名称（不含 #）"},
    },
    is_write=True,
    group="moderation",
    short_description="Archive a private channel.",
)
def archive_private_channel(room_name):
    group = _private_room_info(room_name)
    data = _api_json("POST", "groups.archive", json={"roomId": group.get("_id", "")})
    if data.get("success"):
        return f"私有频道 #{room_name} 已归档。"
    raise ToolExecutionError(f"[错误] 归档私有频道失败: {_format_json(data)}")


@rocketchat_tool(
    "delete_private_channel",
    "删除私有频道及其所有消息。危险操作，不可逆。",
    {
        "room_name": {"type": "string", "description": "要删除的私有频道名称（不含 #）"},
    },
    is_write=True,
    group="moderation",
    short_description="Delete a private channel permanently.",
)
def delete_private_channel(room_name):
    group = _private_room_info(room_name)
    data = _api_json("POST", "rooms.delete", json={"roomId": group.get("_id", "")})
    if data.get("success"):
        return f"私有频道 #{room_name} 已删除。"
    raise ToolExecutionError(f"[错误] 删除私有频道失败: {_format_json(data)}")


@rocketchat_tool(
    "create_direct_message",
    "创建或打开与指定用户的私聊会话。",
    {
        "username": {"type": "string", "description": "对方用户名"},
    },
    is_write=True,
    group="direct_messages",
    short_description="Open a DM room with a user.",
)
def create_direct_message(username):
    data = _api_json("POST", "dm.create", json={"username": username})
    room = data.get("room") or {}
    if not room.get("_id"):
        raise ToolExecutionError(f"[错误] 创建私聊会话失败: {_format_json(data)}")
    return _format_json({
        "id": room.get("_id", ""),
        "name": room.get("name", room.get("fname", "")),
        "usernames": room.get("usernames", []),
    })


@rocketchat_tool(
    "send_direct_message",
    "向指定用户发送私聊消息。",
    {
        "username": {"type": "string", "description": "对方用户名"},
        "text": {"type": "string", "description": "私聊内容"},
    },
    is_write=True,
    group="direct_messages",
    short_description="Send a DM to a user.",
)
def send_direct_message(username, text):
    room = _api_json("POST", "dm.create", json={"username": username}).get("room", {})
    room_id = room.get("_id", "")
    if not room_id:
        raise ToolExecutionError(f"[错误] 无法创建或定位与 {username} 的私聊")
    data = _api_json("POST", "chat.sendMessage", json={"message": {"rid": room_id, "msg": text}})
    if data.get("success"):
        return _format_json(_normalize_message(data.get("message", {})))
    raise ToolExecutionError(f"[错误] 发送私聊失败: {_format_json(data)}")


@rocketchat_tool(
    "create_user",
    "创建新用户账号。",
    {
        "username": {"type": "string", "description": "用户名"},
        "email": {"type": "string", "description": "邮箱地址"},
        "password": {"type": "string", "description": "初始密码"},
        "name": {"type": "string", "description": "显示名称"},
        "roles": {"type": "array", "items": {"type": "string"}, "description": "角色列表，默认 ['user']"},
    },
    is_write=True,
    group="users",
    short_description="Create a workspace user.",
)
def create_user(username, email, password, name, roles=None):
    data = _api_json(
        "POST",
        "users.create",
        json={
            "username": username,
            "email": email,
            "password": password,
            "name": name,
            "roles": roles or ["user"],
            "verified": True,
        },
    )
    user = data.get("user") or {}
    if user.get("_id"):
        return _format_json(_normalize_user(user))
    raise ToolExecutionError(f"[错误] 创建用户失败: {_format_json(data)}")


@rocketchat_tool(
    "set_user_active_status",
    "启用或停用用户账号。",
    {
        "username": {"type": "string", "description": "用户名"},
        "active": {"type": "boolean", "description": "true 表示启用，false 表示停用"},
    },
    is_write=True,
    group="moderation",
    short_description="Enable or disable a user account.",
)
def set_user_active_status(username, active):
    data = _api_json(
        "POST",
        "users.setActiveStatus",
        json={"userId": _user_id(username), "activeStatus": bool(active)},
    )
    if data.get("success"):
        return f"用户 {username} 已{'启用' if active else '停用'}。"
    raise ToolExecutionError(f"[错误] 更新用户状态失败: {_format_json(data)}")


@rocketchat_tool(
    "delete_user",
    "删除一个用户及其数据。极其危险，不可逆。",
    {
        "username": {"type": "string", "description": "要删除的用户名"},
    },
    is_write=True,
    group="moderation",
    short_description="Delete a user account permanently.",
)
def delete_user(username):
    data = _api_json("POST", "users.delete", json={"userId": _user_id(username)})
    if data.get("success"):
        return f"用户 {username} 已被永久删除。"
    raise ToolExecutionError(f"[错误] 删除用户失败: {_format_json(data)}")


@rocketchat_tool(
    "create_incoming_integration",
    "创建 incoming webhook integration。",
    {
        "name": {"type": "string", "description": "integration 名称"},
        "channel": {"type": "string", "description": "目标频道名，例如 #general"},
        "username": {"type": "string", "description": "消息显示用户名"},
        "enabled": {"type": "boolean", "description": "是否启用，默认 true"},
        "script_enabled": {"type": "boolean", "description": "是否启用脚本，默认 false"},
        "script": {"type": "string", "description": "可选脚本内容"},
    },
    is_write=True,
    group="integrations",
    short_description="Create an incoming webhook integration.",
)
def create_incoming_integration(name, channel, username, enabled=True, script_enabled=False, script=""):
    payload = {
        "type": "webhook-incoming",
        "name": name,
        "enabled": bool(enabled),
        "channel": channel,
        "username": username,
        "scriptEnabled": bool(script_enabled),
    }
    if script:
        payload["script"] = script
    data = _api_json("POST", "integrations.create", json=payload)
    if data.get("success"):
        return _format_json(_normalize_integration(data.get("integration", {})))
    raise ToolExecutionError(f"[错误] 创建 incoming integration 失败: {_format_json(data)}")


@rocketchat_tool(
    "create_outgoing_integration",
    "创建 outgoing webhook integration。",
    {
        "name": {"type": "string", "description": "integration 名称"},
        "channel": {"type": "string", "description": "源频道名，例如 #incidents"},
        "username": {"type": "string", "description": "消息显示用户名"},
        "target_urls": {"type": "array", "items": {"type": "string"}, "description": "目标 URL 列表"},
        "event": {"type": "string", "description": "触发事件，默认 sendMessage"},
        "enabled": {"type": "boolean", "description": "是否启用，默认 true"},
        "script_enabled": {"type": "boolean", "description": "是否启用脚本，默认 false"},
        "script": {"type": "string", "description": "可选脚本内容"},
        "trigger_words": {"type": "array", "items": {"type": "string"}, "description": "触发词列表（可选）"},
    },
    is_write=True,
    group="integrations",
    short_description="Create an outgoing webhook integration.",
)
def create_outgoing_integration(
    name,
    channel,
    username,
    target_urls,
    event="sendMessage",
    enabled=True,
    script_enabled=False,
    script="",
    trigger_words=None,
):
    payload = {
        "type": "webhook-outgoing",
        "name": name,
        "enabled": bool(enabled),
        "channel": channel,
        "username": username,
        "urls": target_urls,
        "event": event,
        "scriptEnabled": bool(script_enabled),
        "triggerWords": trigger_words or [],
    }
    if script:
        payload["script"] = script
    data = _api_json("POST", "integrations.create", json=payload)
    if data.get("success"):
        return _format_json(_normalize_integration(data.get("integration", {})))
    raise ToolExecutionError(f"[错误] 创建 outgoing integration 失败: {_format_json(data)}")


@rocketchat_tool(
    "remove_integration",
    "删除一个 integration。危险操作，不可逆。",
    {
        "name": {"type": "string", "description": "integration 名称"},
        "integration_type": {"type": "string", "description": "integration 类型，例如 webhook-incoming 或 webhook-outgoing"},
    },
    is_write=True,
    group="integrations",
    short_description="Remove an existing integration.",
)
def remove_integration(name, integration_type=""):
    integration = _find_integration(name=name, integration_type=integration_type)
    integration_id = integration.get("_id", integration.get("integrationId", ""))
    if not integration_id:
        raise ToolExecutionError(f"[错误] 找不到 integration ID: {name}")
    data = _api_json("POST", "integrations.remove", json={"integrationId": integration_id})
    if data.get("success"):
        return f"integration {name} 已删除。"
    raise ToolExecutionError(f"[错误] 删除 integration 失败: {_format_json(data)}")
