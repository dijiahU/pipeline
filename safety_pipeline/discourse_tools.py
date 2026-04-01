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


def discourse_tool(name, description, params, required=None, is_write=False, group="", short_description=""):
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


def _latest_payload():
    return _api_json("GET", "latest.json") or {}


def _latest_topics():
    return ((_latest_payload().get("topic_list") or {}).get("topics") or [])


def _categories():
    payload = _api_json("GET", "categories.json") or {}
    categories = ((payload.get("category_list") or {}).get("categories") or []) if isinstance(payload, dict) else []
    return {
        int(item["id"]): {
            "id": int(item["id"]),
            "name": item.get("name", ""),
            "slug": item.get("slug", ""),
            "topic_count": int(item.get("topic_count", 0) or 0),
            "description": item.get("description_text") or item.get("description") or "",
            "read_restricted": bool(item.get("read_restricted", False)),
        }
        for item in categories
        if isinstance(item, dict) and item.get("id") is not None
    }


def _category_meta_by_name(name):
    needle = str(name or "").strip().lower()
    if not needle:
        return None
    for category in _categories().values():
        if needle in {category.get("name", "").lower(), category.get("slug", "").lower(), str(category.get("id", "")).lower()}:
            return category
    return None


def _category_id_by_name(name):
    category = _category_meta_by_name(name)
    return int(category["id"]) if category else None


def _category_detail_payload(category):
    meta = _category_meta_by_name(category)
    if not meta:
        raise ToolExecutionError(f"[错误] 找不到分类: {category}")
    return _api_json("GET", f"c/{int(meta['id'])}/show.json") or {}


def _category_topics_payload(category):
    meta = _category_meta_by_name(category)
    if not meta:
        raise ToolExecutionError(f"[错误] 找不到分类: {category}")
    return _api_json("GET", f"c/{meta['slug']}/{int(meta['id'])}.json") or {}


def _is_category_meta_topic(topic):
    title = str((topic or {}).get("title", "")).strip().lower()
    return title.startswith("about the ") and title.endswith(" category")


def _topic_summary_from_listing(topic):
    categories = _categories()
    category = categories.get(int(topic.get("category_id", 0) or 0), {})
    return {
        "id": topic.get("id"),
        "title": topic.get("title", ""),
        "category": category.get("slug") or category.get("name", ""),
        "pinned": bool(topic.get("pinned") or topic.get("pinned_globally")),
        "closed": bool(topic.get("closed", False)),
        "post_count": int(topic.get("posts_count", 0) or 0),
        "tags": list(topic.get("tags") or []),
        "last_poster_username": topic.get("last_poster_username", ""),
        "created_at": topic.get("created_at", ""),
    }


def _topic_detail(payload):
    categories = _categories()
    category = categories.get(int(payload.get("category_id", 0) or 0), {})
    posts = payload.get("post_stream", {}).get("posts", [])
    return {
        "id": payload.get("id"),
        "title": payload.get("title", ""),
        "category": category.get("slug") or category.get("name", ""),
        "pinned": bool(payload.get("pinned") or payload.get("pinned_globally")),
        "closed": bool(payload.get("closed", False)),
        "post_count": len(posts),
        "tags": list(payload.get("tags") or []),
        "posts": [
            {
                "id": item.get("id"),
                "post_number": item.get("post_number"),
                "username": item.get("username", ""),
                "raw": item.get("raw", ""),
                "created_at": item.get("created_at", ""),
            }
            for item in posts
        ],
    }


def _topic_payload(topic_id):
    resp = _api("GET", f"t/{int(topic_id)}.json", expect_ok=False, params={"include_raw": "1"})
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        raise ToolExecutionError(f"[Discourse API 错误] {resp.status_code}: {resp.text[:500]}")
    return resp.json()


def _search_payload(query):
    return _api_json("GET", "search.json", params={"q": query}) or {}


def _find_topic_by_title(title):
    needle = str(title or "").strip()
    if not needle:
        return None
    for topic in _latest_topics():
        if str(topic.get("title", "")).strip() == needle:
            return topic
    search = _search_payload(needle)
    for topic in search.get("topics") or []:
        if str(topic.get("title", "")).strip() == needle:
            return topic
    return None


def _list_admin_users():
    payload = _api_json("GET", "admin/users/list/active.json?page=0") or []
    return payload if isinstance(payload, list) else []


def _user_id_by_username(username):
    needle = str(username or "").strip().lower()
    if not needle:
        return None
    for user in _list_admin_users():
        if str(user.get("username", "")).strip().lower() == needle:
            return int(user.get("id"))
    return None


def _list_topics_filtered(category="", status=""):
    category_id = _category_id_by_name(category) if category else None
    normalized_status = str(status or "").strip().lower()
    results = []
    for topic in _latest_topics():
        if category_id is not None and int(topic.get("category_id", -1)) != category_id:
            continue
        closed = bool(topic.get("closed", False))
        if normalized_status == "open" and closed:
            continue
        if normalized_status == "closed" and not closed:
            continue
        results.append(_topic_summary_from_listing(topic))
    return results


def _set_topic_pinned_state(topic_id, pinned):
    _api(
        "PUT",
        f"t/{int(topic_id)}/status",
        data={"status": "pinned", "enabled": "true" if pinned else "false"},
    )
    payload = _topic_payload(topic_id)
    if not payload:
        raise ToolExecutionError(f"[错误] 找不到主题: {topic_id}")
    return {
        "topic_id": int(topic_id),
        "pinned": bool(payload.get("pinned") or payload.get("pinned_globally")),
    }


def _set_topic_closed_state(topic_id, closed):
    _api(
        "PUT",
        f"t/{int(topic_id)}/status",
        data={"status": "closed", "enabled": "true" if closed else "false"},
    )
    payload = _topic_payload(topic_id)
    if not payload:
        raise ToolExecutionError(f"[错误] 找不到主题: {topic_id}")
    return {
        "topic_id": int(topic_id),
        "closed": bool(payload.get("closed", False)),
    }


@discourse_tool(
    "list_topics",
    "列出论坛主题，可按分类或状态筛选。",
    {
        "category": {"type": "string", "description": "分类名称，如 announcements、support、product"},
        "status": {"type": "string", "description": "状态筛选，可选 open 或 closed"},
    },
)
def list_topics(category="", status=""):
    return _format_json(_list_topics_filtered(category=category, status=status))


@discourse_tool(
    "list_open_topics",
    "列出开放状态的论坛主题，可按分类筛选。",
    {
        "category": {"type": "string", "description": "分类名称，如 announcements、support、product"},
    },
)
def list_open_topics(category=""):
    return _format_json(_list_topics_filtered(category=category, status="open"))


@discourse_tool(
    "list_closed_topics",
    "列出已关闭的论坛主题，可按分类筛选。",
    {
        "category": {"type": "string", "description": "分类名称，如 announcements、support、product"},
    },
)
def list_closed_topics(category=""):
    return _format_json(_list_topics_filtered(category=category, status="closed"))


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
    return _format_json(_topic_detail(payload))


@discourse_tool(
    "get_topic_by_title",
    "按标题精确获取主题详情。",
    {
        "title": {"type": "string", "description": "主题标题"},
    },
    required=["title"],
)
def get_topic_by_title(title):
    match = _find_topic_by_title(title)
    if not match:
        raise ToolExecutionError(f"[错误] 找不到主题标题: {title}")
    return get_topic(match.get("id"))


@discourse_tool(
    "list_topic_posts",
    "列出指定主题下的帖子明细。",
    {
        "topic_id": {"type": "integer", "description": "主题 ID"},
    },
    required=["topic_id"],
)
def list_topic_posts(topic_id):
    payload = _topic_payload(topic_id)
    if not payload:
        raise ToolExecutionError(f"[错误] 找不到主题: {topic_id}")
    detail = _topic_detail(payload)
    return _format_json(detail.get("posts", []))


@discourse_tool(
    "list_users",
    "列出社区用户。",
    {},
)
def list_users():
    results = []
    for user in _list_admin_users():
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
    "list_staff_users",
    "列出管理员和版主用户。",
    {},
)
def list_staff_users():
    payload = json.loads(list_users())
    return _format_json([user for user in payload if user.get("admin") or user.get("moderator")])


@discourse_tool(
    "get_user",
    "获取指定用户的详细信息。",
    {
        "username": {"type": "string", "description": "用户名"},
    },
    required=["username"],
)
def get_user(username):
    payload = _api_json("GET", f"u/{username}.json")
    user = payload.get("user", {}) if isinstance(payload, dict) else {}
    return _format_json(
        {
            "id": user.get("id"),
            "username": user.get("username", ""),
            "name": user.get("name", ""),
            "admin": bool(user.get("admin", False)),
            "moderator": bool(user.get("moderator", False)),
            "trust_level": user.get("trust_level", 0),
            "created_at": user.get("created_at", ""),
            "post_count": user.get("post_count", 0),
            "topic_count": user.get("topic_count", 0),
        }
    )


@discourse_tool(
    "list_user_posts",
    "列出指定用户最近的发帖记录。",
    {
        "username": {"type": "string", "description": "用户名"},
    },
    required=["username"],
)
def list_user_posts(username):
    payload = _api_json("GET", f"u/{username}/activity.json") or []
    results = []
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict):
            continue
        results.append(
            {
                "post_id": item.get("id"),
                "topic_id": item.get("topic_id"),
                "topic_slug": item.get("topic_slug", ""),
                "excerpt": item.get("excerpt") or item.get("raw") or "",
                "created_at": item.get("created_at", ""),
            }
        )
    return _format_json(results)


@discourse_tool(
    "list_user_topics",
    "列出指定用户创建的主题。",
    {
        "username": {"type": "string", "description": "用户名"},
    },
    required=["username"],
)
def list_user_topics(username):
    needle = str(username or "").strip().lower()
    results = []
    for topic in _latest_topics():
        payload = _topic_payload(topic.get("id"))
        if not payload:
            continue
        posts = payload.get("post_stream", {}).get("posts", [])
        first_post = posts[0] if posts else {}
        if str(first_post.get("username", "")).strip().lower() != needle:
            continue
        results.append(_topic_summary_from_listing(topic))
    return _format_json(results)


@discourse_tool(
    "list_categories",
    "列出论坛的所有分类。",
    {},
)
def list_categories():
    results = []
    for cat in _categories().values():
        results.append(
            {
                "id": cat["id"],
                "name": cat["name"],
                "slug": cat["slug"],
                "topic_count": cat["topic_count"],
            }
        )
    return _format_json(results)


@discourse_tool(
    "get_category",
    "获取指定分类的详细信息。",
    {
        "category": {"type": "string", "description": "分类名称、slug 或 ID"},
    },
    required=["category"],
)
def get_category(category):
    meta = _category_meta_by_name(category)
    payload = _category_detail_payload(category)
    category_payload = payload.get("category") or {}
    return _format_json(
        {
            "id": category_payload.get("id", meta.get("id") if meta else None),
            "name": category_payload.get("name", meta.get("name") if meta else ""),
            "slug": category_payload.get("slug", meta.get("slug") if meta else ""),
            "topic_count": int(category_payload.get("topic_count", meta.get("topic_count", 0) if meta else 0) or 0),
            "description": category_payload.get("description_text") or category_payload.get("description") or "",
            "read_restricted": bool(category_payload.get("read_restricted", False)),
        }
    )


@discourse_tool(
    "list_category_topics",
    "列出指定分类下的主题。",
    {
        "category": {"type": "string", "description": "分类名称、slug 或 ID"},
        "include_about_topics": {"type": "boolean", "description": "是否包含系统自动生成的 about 分类主题"},
    },
    required=["category"],
)
def list_category_topics(category, include_about_topics=False):
    payload = _category_topics_payload(category)
    topics = (payload.get("topic_list") or {}).get("topics") or []
    results = []
    for topic in topics:
        if not include_about_topics and _is_category_meta_topic(topic):
            continue
        results.append(_topic_summary_from_listing(topic))
    return _format_json(results)


@discourse_tool(
    "search_topics",
    "搜索论坛主题。",
    {
        "query": {"type": "string", "description": "搜索关键词"},
    },
    required=["query"],
)
def search_topics(query):
    payload = _search_payload(query)
    topics = payload.get("topics") or [] if isinstance(payload, dict) else []
    results = []
    for topic in topics:
        if not isinstance(topic, dict):
            continue
        results.append(_topic_summary_from_listing(topic))
    return _format_json(results)


@discourse_tool(
    "search_posts",
    "搜索论坛帖子内容。",
    {
        "query": {"type": "string", "description": "搜索关键词"},
    },
    required=["query"],
)
def search_posts(query):
    payload = _search_payload(query)
    topic_titles = {item.get("id"): item.get("title", "") for item in payload.get("topics") or [] if isinstance(item, dict)}
    results = []
    for post in payload.get("posts") or []:
        if not isinstance(post, dict):
            continue
        results.append(
            {
                "post_id": post.get("id"),
                "topic_id": post.get("topic_id"),
                "topic_title": topic_titles.get(post.get("topic_id"), ""),
                "username": post.get("username", ""),
                "blurb": post.get("blurb", ""),
                "created_at": post.get("created_at", ""),
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
    "create_topic",
    "创建一个新的论坛主题。",
    {
        "title": {"type": "string", "description": "主题标题"},
        "raw": {"type": "string", "description": "主题内容（第一条帖子）"},
        "category": {"type": "string", "description": "分类名称"},
        "username": {"type": "string", "description": "发帖用户名"},
    },
    required=["title", "raw", "category"],
    is_write=True,
)
def create_topic(title, raw, category="", username="admin"):
    data = {"title": title, "raw": raw}
    if category:
        cat_id = _category_id_by_name(category)
        if cat_id is None:
            raise ToolExecutionError(f"[错误] 找不到分类: {category}")
        data["category"] = cat_id
    payload = _api_json("POST", "posts.json", username=username, data=data)
    return _format_json(
        {
            "topic_id": payload.get("topic_id"),
            "post_id": payload.get("id"),
            "title": title,
            "category": category,
            "username": username,
        }
    )


@discourse_tool(
    "rename_topic",
    "修改主题标题。",
    {
        "topic_id": {"type": "integer", "description": "主题 ID"},
        "new_title": {"type": "string", "description": "新的主题标题"},
    },
    required=["topic_id", "new_title"],
    is_write=True,
)
def rename_topic(topic_id, new_title):
    _api_json("PUT", f"t/{int(topic_id)}.json", data={"title": new_title})
    payload = _topic_payload(topic_id)
    if not payload:
        raise ToolExecutionError(f"[错误] 找不到主题: {topic_id}")
    return _format_json(
        {
            "topic_id": int(topic_id),
            "title": payload.get("title", ""),
            "category": (_categories().get(int(payload.get("category_id", 0) or 0), {}) or {}).get("slug", ""),
        }
    )


@discourse_tool(
    "move_topic_to_category",
    "将主题移动到新的分类。",
    {
        "topic_id": {"type": "integer", "description": "主题 ID"},
        "category": {"type": "string", "description": "目标分类名称、slug 或 ID"},
    },
    required=["topic_id", "category"],
    is_write=True,
)
def move_topic_to_category(topic_id, category):
    meta = _category_meta_by_name(category)
    if not meta:
        raise ToolExecutionError(f"[错误] 找不到分类: {category}")
    _api_json("PUT", f"t/{int(topic_id)}.json", data={"category_id": int(meta["id"])})
    payload = _topic_payload(topic_id)
    if not payload:
        raise ToolExecutionError(f"[错误] 找不到主题: {topic_id}")
    current_category = (_categories().get(int(payload.get("category_id", 0) or 0), {}) or {}).get("slug", "")
    return _format_json(
        {
            "topic_id": int(topic_id),
            "category": current_category,
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
    return _format_json(_set_topic_pinned_state(topic_id, pinned))


@discourse_tool(
    "unpin_topic",
    "取消主题置顶。",
    {
        "topic_id": {"type": "integer", "description": "主题 ID"},
    },
    required=["topic_id"],
    is_write=True,
)
def unpin_topic(topic_id):
    return _format_json(_set_topic_pinned_state(topic_id, False))


@discourse_tool(
    "close_topic",
    "关闭或重新打开论坛主题。",
    {
        "topic_id": {"type": "integer", "description": "主题 ID"},
        "closed": {"type": "boolean", "description": "true 关闭，false 重新打开"},
    },
    required=["topic_id"],
    is_write=True,
)
def close_topic(topic_id, closed=True):
    return _format_json(_set_topic_closed_state(topic_id, closed))


@discourse_tool(
    "reopen_topic",
    "重新打开已关闭的论坛主题。",
    {
        "topic_id": {"type": "integer", "description": "主题 ID"},
    },
    required=["topic_id"],
    is_write=True,
)
def reopen_topic(topic_id):
    return _format_json(_set_topic_closed_state(topic_id, False))


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


@discourse_tool(
    "create_category",
    "创建一个新的论坛分类。",
    {
        "name": {"type": "string", "description": "分类名称"},
        "color": {"type": "string", "description": "分类颜色，十六进制字符串，如 0088CC"},
        "text_color": {"type": "string", "description": "文字颜色，十六进制字符串，如 FFFFFF"},
    },
    required=["name"],
    is_write=True,
)
def create_category(name, color="6E6E6E", text_color="FFFFFF"):
    payload = _api_json(
        "POST",
        "categories.json",
        data={
            "name": name,
            "color": color,
            "text_color": text_color,
        },
    )
    category = payload.get("category") if isinstance(payload, dict) else None
    if not category:
        raise ToolExecutionError(f"[错误] 创建分类失败: {name}")
    return _format_json(
        {
            "id": category.get("id"),
            "name": category.get("name", ""),
            "slug": category.get("slug", ""),
        }
    )


@discourse_tool(
    "suspend_user",
    "暂停用户账号，阻止其发帖和登录。",
    {
        "user_id": {"type": "integer", "description": "用户 ID"},
        "duration_days": {"type": "integer", "description": "暂停天数"},
        "reason": {"type": "string", "description": "暂停原因"},
    },
    required=["user_id", "reason"],
    is_write=True,
)
def suspend_user(user_id, reason, duration_days=365):
    import datetime as _dt

    suspend_until = (_dt.datetime.utcnow() + _dt.timedelta(days=duration_days)).strftime("%Y-%m-%d")
    payload = _api_json(
        "PUT",
        f"admin/users/{int(user_id)}/suspend.json",
        json={"suspend_until": suspend_until, "reason": reason},
    ) or {}
    suspension = payload.get("suspension") or {}
    return _format_json(
        {
            "user_id": int(user_id),
            "suspended": True,
            "reason": reason,
            "suspend_until": suspension.get("suspended_till", suspend_until),
        }
    )


@discourse_tool(
    "unsuspend_user",
    "解除用户暂停状态。",
    {
        "user_id": {"type": "integer", "description": "用户 ID"},
    },
    required=["user_id"],
    is_write=True,
)
def unsuspend_user(user_id):
    payload = _api_json("PUT", f"admin/users/{int(user_id)}/unsuspend.json") or {}
    suspension = payload.get("suspension") or {}
    return _format_json(
        {
            "user_id": int(user_id),
            "suspended": bool(suspension.get("suspended_till")),
        }
    )
