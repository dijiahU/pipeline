"""
Discourse REST API 工具注册。
"""

import json
import os

from .exceptions import ToolExecutionError
from .service_tools import ServiceToolRegistry

try:
    import requests
except ModuleNotFoundError:
    requests = None


_REGISTRY = ServiceToolRegistry(service_id="discourse")


def discourse_tool(name, description, params, required=None, is_write=False):
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


def _base_url():
    return os.environ.get("DISCOURSE_BASE_URL", "http://localhost:4200").rstrip("/")


def _api_key():
    return os.environ.get("DISCOURSE_API_KEY", "")


def _api_username():
    return os.environ.get("DISCOURSE_API_USERNAME", "admin")


def _headers(username=""):
    return {
        "Api-Key": _api_key(),
        "Api-Username": username or _api_username(),
        "Accept": "application/json",
    }


def _api(method, path, *, username="", expect_ok=True, **kwargs):
    _require_requests()
    url = f"{_base_url()}/{path.lstrip('/')}"
    kwargs.setdefault("headers", _headers(username=username))
    kwargs.setdefault("timeout", 30)
    try:
        resp = requests.request(method, url, **kwargs)
    except requests.RequestException as exc:
        raise ToolExecutionError(f"[Discourse 请求失败] {type(exc).__name__}: {exc}") from exc
    if expect_ok and resp.status_code >= 400:
        raise ToolExecutionError(f"[Discourse API 错误] {resp.status_code}: {resp.text[:500]}")
    return resp


def _api_json(method, path, **kwargs):
    resp = _api(method, path, **kwargs)
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


def _categories():
    payload = _api_json("GET", "categories.json") or {}
    categories = ((payload.get("category_list") or {}).get("categories") or []) if isinstance(payload, dict) else []
    return {
        int(item["id"]): {
            "id": item["id"],
            "name": item.get("name", ""),
            "slug": item.get("slug", ""),
        }
        for item in categories
        if isinstance(item, dict) and item.get("id") is not None
    }


def _category_id_by_name(name):
    for category in _categories().values():
        if category.get("name", "").lower() == name.lower() or category.get("slug", "").lower() == name.lower():
            return int(category["id"])
    return None


def _topic_summary_from_latest(topic):
    categories = _categories()
    category = categories.get(int(topic.get("category_id", 0)), {})
    return {
        "id": topic.get("id"),
        "title": topic.get("title", ""),
        "category": category.get("slug") or category.get("name", ""),
        "pinned": bool(topic.get("pinned") or topic.get("pinned_globally")),
        "post_count": int(topic.get("posts_count", 0)),
    }


def _topic_payload(topic_id):
    resp = _api("GET", f"t/{int(topic_id)}.json", expect_ok=False)
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        raise ToolExecutionError(f"[Discourse API 错误] {resp.status_code}: {resp.text[:500]}")
    return resp.json()


@discourse_tool(
    "list_topics",
    "列出论坛主题，可按分类筛选。",
    {
        "category": {"type": "string", "description": "分类名称，如 announcements、support"},
    },
)
def list_topics(category=""):
    category_id = _category_id_by_name(category) if category else None
    payload = _api_json("GET", "latest.json") or {}
    results = []
    for topic in (payload.get("topic_list") or {}).get("topics", []):
        if category_id is not None and int(topic.get("category_id", -1)) != category_id:
            continue
        results.append(_topic_summary_from_latest(topic))
    return _format_json(results)


@discourse_tool(
    "get_topic",
    "获取单个主题详情。",
    {
        "topic_id": {"type": "integer", "description": "主题 ID"},
    },
)
def get_topic(topic_id):
    payload = _topic_payload(topic_id)
    if not payload:
        raise ToolExecutionError(f"[错误] 找不到主题: {topic_id}")
    categories = _categories()
    category = categories.get(int(payload.get("category_id", 0)), {})
    posts = payload.get("post_stream", {}).get("posts", [])
    return _format_json(
        {
            "id": payload.get("id"),
            "title": payload.get("title", ""),
            "category": category.get("slug") or category.get("name", ""),
            "pinned": bool(payload.get("pinned") or payload.get("pinned_globally")),
            "post_count": len(posts),
            "posts": [
                {
                    "id": item.get("id"),
                    "username": item.get("username", ""),
                    "raw": item.get("raw", ""),
                }
                for item in posts
            ],
        }
    )


@discourse_tool(
    "list_users",
    "列出社区用户。",
    {},
)
def list_users():
    payload = _api_json("GET", "admin/users/list/active.json?page=0") or []
    results = []
    for user in payload:
        if not isinstance(user, dict):
            continue
        username = user.get("username", "")
        if username in {"system", "discobot"}:
            continue
        results.append(
            {
                "id": user.get("id"),
                "username": username,
                "name": user.get("name", ""),
                "admin": bool(user.get("admin", False)),
                "moderator": bool(user.get("moderator", False)),
                "active": bool(user.get("active", False)),
            }
        )
    return _format_json(results)


@discourse_tool(
    "create_post",
    "在指定主题下追加一条回复。",
    {
        "topic_id": {"type": "integer", "description": "主题 ID"},
        "raw": {"type": "string", "description": "回复内容"},
        "username": {"type": "string", "description": "发帖用户名"},
    },
    required=["topic_id", "raw"],
    is_write=True,
)
def create_post(topic_id, raw, username="admin"):
    payload = _api_json(
        "POST",
        "posts.json",
        username=username,
        data={
            "topic_id": int(topic_id),
            "raw": raw,
        },
    )
    return _format_json(
        {
            "id": payload.get("id"),
            "topic_id": payload.get("topic_id"),
            "username": username,
            "raw": raw,
        }
    )


@discourse_tool(
    "set_topic_pinned",
    "设置主题是否置顶。",
    {
        "topic_id": {"type": "integer", "description": "主题 ID"},
        "pinned": {"type": "boolean", "description": "是否置顶"},
    },
    required=["topic_id"],
    is_write=True,
)
def set_topic_pinned(topic_id, pinned=True):
    _api(
        "PUT",
        f"t/{int(topic_id)}/status",
        data={"status": "pinned", "enabled": "true" if pinned else "false"},
    )
    payload = _topic_payload(topic_id)
    if not payload:
        raise ToolExecutionError(f"[错误] 找不到主题: {topic_id}")
    return _format_json(
        {
            "topic_id": int(topic_id),
            "pinned": bool(payload.get("pinned") or payload.get("pinned_globally")),
        }
    )


@discourse_tool(
    "delete_topic",
    "删除一个论坛主题。",
    {
        "topic_id": {"type": "integer", "description": "主题 ID"},
    },
    required=["topic_id"],
    is_write=True,
)
def delete_topic(topic_id):
    _api("DELETE", f"t/{int(topic_id)}.json")
    return _format_json({"deleted_topic_id": int(topic_id)})
