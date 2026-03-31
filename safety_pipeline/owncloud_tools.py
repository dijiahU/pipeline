"""
ownCloud (oCIS) WebDAV + OCS 工具注册。

通过 WebDAV 进行文件/文件夹操作，通过 OCS API 管理共享链接。
公共接口（给 environment.py 调用）:
  get_all_schemas() -> list
  call_tool(name, args) -> str
  get_tool_names() -> list
  get_write_tool_names() -> list
"""

import json
import os
import xml.etree.ElementTree as ET
from urllib.parse import quote, unquote, urlparse

from .exceptions import ToolExecutionError
from .service_tools import ServiceToolRegistry

try:
    import requests
    from requests.auth import HTTPBasicAuth
except ModuleNotFoundError:
    requests = None
    HTTPBasicAuth = None

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


_config = {
    "base_url": os.environ.get("OWNCLOUD_BASE_URL", "https://localhost:9200").rstrip("/"),
    "admin_user": os.environ.get("OWNCLOUD_ADMIN_USER", "admin"),
    "admin_password": os.environ.get("OWNCLOUD_ADMIN_PASSWORD", "Admin123!"),
    "default_public_link_password": os.environ.get("OWNCLOUD_PUBLIC_LINK_PASSWORD", "Share123!"),
}

_REGISTRY = ServiceToolRegistry(service_id="owncloud")

_SHARE_TYPE_LABELS = {
    0: "user",
    3: "public_link",
}


def owncloud_tool(name, description, params, required=None, is_write=False, group="", short_description=""):
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
        raise ToolExecutionError("requests 库未安装，无法调用 ownCloud API。pip install requests")


def _auth():
    return HTTPBasicAuth(_config["admin_user"], _config["admin_password"])


def _normalize_path(path=""):
    path = (path or "").replace("\\", "/").strip()
    if path in ("", "/"):
        return ""
    parts = [segment for segment in path.split("/") if segment and segment != "."]
    return "/".join(parts)


def _path_parent(path):
    normalized = _normalize_path(path)
    if not normalized or "/" not in normalized:
        return ""
    return normalized.rsplit("/", 1)[0]


def _join_path(*parts):
    segments = []
    for part in parts:
        normalized = _normalize_path(part)
        if normalized:
            segments.extend(normalized.split("/"))
    return "/".join(segments)


def _encode_path(path=""):
    normalized = _normalize_path(path)
    if not normalized:
        return ""
    return "/".join(quote(segment, safe="") for segment in normalized.split("/"))


def _webdav_url(path=""):
    user = quote(_config["admin_user"], safe="")
    encoded_path = _encode_path(path)
    suffix = f"/{encoded_path}" if encoded_path else ""
    return f"{_config['base_url']}/dav/files/{user}{suffix}"


def _ocs_url(path=""):
    return f"{_config['base_url']}/{path.lstrip('/')}"


def _api(method, url, **kwargs):
    _require_requests()
    kwargs.setdefault("auth", _auth())
    kwargs.setdefault("verify", False)
    kwargs.setdefault("timeout", 15)
    try:
        return requests.request(method, url, **kwargs)
    except requests.RequestException as exc:
        raise ToolExecutionError(f"[ownCloud 请求失败] {type(exc).__name__}: {exc}") from exc


def _format_json(data):
    if isinstance(data, str):
        return data
    return json.dumps(data, ensure_ascii=False, indent=2)


def _coerce_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _href_to_relative_path(href):
    parsed = urlparse(href)
    raw_path = parsed.path or href or ""
    decoded = unquote(raw_path)
    user = _config["admin_user"]
    prefix = f"/dav/files/{user}/"
    root_prefix = f"/dav/files/{user}"
    if decoded.startswith(prefix):
        return decoded[len(prefix):].strip("/")
    if decoded.startswith(root_prefix):
        return decoded[len(root_prefix):].strip("/")
    return decoded.strip("/")


def _parse_propfind_entries(xml_text):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    ns = {
        "d": "DAV:",
        "oc": "http://owncloud.org/ns",
    }
    entries = []
    for response in root.findall("d:response", ns):
        href = response.findtext("d:href", "", ns)
        propstat = response.find("d:propstat", ns)
        if propstat is None:
            continue
        prop = propstat.find("d:prop", ns)
        if prop is None:
            continue
        resource_type = prop.find("d:resourcetype", ns)
        is_dir = resource_type is not None and resource_type.find("d:collection", ns) is not None
        size = prop.findtext("d:getcontentlength", "0", ns)
        last_modified = prop.findtext("d:getlastmodified", "", ns)
        display_name = prop.findtext("d:displayname", "", ns)
        relative_path = _href_to_relative_path(href)
        entries.append(
            {
                "href": href,
                "path": relative_path,
                "name": display_name or (relative_path.rstrip("/").split("/")[-1] if relative_path else "/"),
                "type": "directory" if is_dir else "file",
                "size": _coerce_int(size, 0),
                "last_modified": last_modified,
                "etag": prop.findtext("d:getetag", "", ns),
                "file_id": prop.findtext("oc:fileid", "", ns),
            }
        )
    return entries


def _child_entries(path, entries):
    normalized = _normalize_path(path)
    return [entry for entry in entries if entry.get("path", "") != normalized]


def _ocs_headers(extra=None):
    headers = {
        "OCS-APIREQUEST": "true",
        "Accept": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def _normalize_share(share):
    share_type = _coerce_int(share.get("share_type"), -1)
    permissions = _coerce_int(share.get("permissions"), 0)
    return {
        "id": str(share.get("id", "")),
        "path": (share.get("path") or "").lstrip("/"),
        "share_type": share_type,
        "share_type_label": _SHARE_TYPE_LABELS.get(share_type, f"type_{share_type}"),
        "permissions": permissions,
        "name": share.get("name", ""),
        "url": share.get("url", ""),
        "token": share.get("token", ""),
        "share_with": share.get("share_with", ""),
        "expiration": share.get("expiration", ""),
    }


def _strip_xml_ns(tag):
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _xml_node_to_value(node):
    children = [child for child in list(node) if isinstance(child.tag, str)]
    if not children:
        return (node.text or "").strip()

    grouped = {}
    for child in children:
        key = _strip_xml_ns(child.tag)
        grouped.setdefault(key, []).append(_xml_node_to_value(child))

    if len(grouped) == 1:
        only_key = next(iter(grouped))
        values = grouped[only_key]
        if len(values) > 1:
            return values
    return {
        key: values[0] if len(values) == 1 else values
        for key, values in grouped.items()
    }


def _extract_ocs_data(resp, action_label):
    try:
        return (resp.json().get("ocs", {}) or {}).get("data", [])
    except ValueError:
        pass

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as exc:
        raise ToolExecutionError(f"[错误] {action_label} 返回了无效响应: {resp.text[:300]}") from exc

    data_node = None
    for child in root.iter():
        if _strip_xml_ns(child.tag) == "data":
            data_node = child
            break
    if data_node is None:
        raise ToolExecutionError(f"[错误] {action_label} 返回中缺少 data 节点: {resp.text[:300]}")
    return _xml_node_to_value(data_node)


def _list_shares_data(path=""):
    url = _ocs_url("ocs/v2.php/apps/files_sharing/api/v1/shares")
    params = {"format": "json"}
    normalized = _normalize_path(path)
    if normalized:
        params["path"] = f"/{normalized}"
    resp = _api("GET", url, params=params, headers=_ocs_headers())
    if resp.status_code != 200:
        raise ToolExecutionError(f"[错误] 获取共享列表失败: {resp.status_code}: {resp.text[:300]}")
    data = _extract_ocs_data(resp, "获取共享列表")
    if isinstance(data, dict):
        data = [data]
    return [_normalize_share(item) for item in (data or [])]


def _get_share_data(share_id):
    url = _ocs_url(f"ocs/v2.php/apps/files_sharing/api/v1/shares/{share_id}")
    resp = _api("GET", url, params={"format": "json"}, headers=_ocs_headers())
    if resp.status_code == 404:
        raise ToolExecutionError(f"[错误] 共享链接不存在: {share_id}")
    if resp.status_code != 200:
        raise ToolExecutionError(f"[错误] 获取共享详情失败: {resp.status_code}: {resp.text[:300]}")
    data = _extract_ocs_data(resp, "获取共享详情")
    if isinstance(data, list):
        if not data:
            raise ToolExecutionError(f"[错误] 未找到共享详情: {share_id}")
        data = data[0]
    return _normalize_share(data)


def _create_share_payload(path, share_type, name="", permissions=1, password="", expire_date="", share_with=""):
    payload = {
        "path": f"/{_normalize_path(path)}",
        "shareType": str(share_type),
        "permissions": str(permissions),
    }
    if name:
        payload["name"] = name
    if password:
        payload["password"] = password
    if expire_date:
        payload["expireDate"] = expire_date
    if share_with:
        payload["shareWith"] = share_with
    return payload


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


@owncloud_tool(
    "list_files",
    "列出指定目录下的文件和子目录。",
    {
        "path": {
            "type": "string",
            "description": "目录路径，如 'Documents'。空字符串或 '/' 表示根目录。",
        },
        "depth": {
            "type": "string",
            "description": "列出深度：'1' 仅当前层，'infinity' 递归列出所有。",
        },
    },
    group="file_browse",
    short_description="List the files and folders under a directory.",
)
def list_files(path="", depth="1"):
    normalized = _normalize_path(path)
    url = _webdav_url(normalized)
    resp = _api("PROPFIND", url, headers={"Depth": depth})
    if resp.status_code == 404:
        raise ToolExecutionError(f"[错误] 路径不存在: {normalized or '/'}")
    if resp.status_code != 207:
        raise ToolExecutionError(f"[错误] PROPFIND {normalized or '/'} -> {resp.status_code}: {resp.text[:300]}")
    entries = _child_entries(normalized, _parse_propfind_entries(resp.text))
    return _format_json(entries)


@owncloud_tool(
    "list_directory_tree",
    "递归列出目录树中的所有文件和子目录。",
    {
        "path": {
            "type": "string",
            "description": "目录路径，如 'Projects/Website-Redesign'。",
        },
    },
    group="file_browse",
    short_description="Recursively list a directory tree.",
)
def list_directory_tree(path):
    return list_files(path=path, depth="infinity")


@owncloud_tool(
    "read_file",
    "读取指定文件的内容。",
    {
        "path": {
            "type": "string",
            "description": "文件路径，如 'Documents/README.md'。",
        },
    },
    required=["path"],
    group="file_browse",
    short_description="Read the text content of a file.",
)
def read_file(path):
    normalized = _normalize_path(path)
    url = _webdav_url(normalized)
    resp = _api("GET", url)
    if resp.status_code == 404:
        raise ToolExecutionError(f"[错误] 文件不存在: {normalized}")
    if resp.status_code != 200:
        raise ToolExecutionError(f"[错误] GET {normalized} -> {resp.status_code}: {resp.text[:300]}")
    return resp.text


@owncloud_tool(
    "file_info",
    "获取文件或目录的详细属性信息。",
    {
        "path": {
            "type": "string",
            "description": "文件或目录路径。",
        },
    },
    required=["path"],
    group="file_browse",
    short_description="Inspect metadata for a file or folder.",
)
def file_info(path):
    normalized = _normalize_path(path)
    url = _webdav_url(normalized)
    resp = _api("PROPFIND", url, headers={"Depth": "0"})
    if resp.status_code == 404:
        raise ToolExecutionError(f"[错误] 路径不存在: {normalized}")
    if resp.status_code != 207:
        raise ToolExecutionError(f"[错误] PROPFIND {normalized} -> {resp.status_code}: {resp.text[:300]}")
    entries = _parse_propfind_entries(resp.text)
    if entries:
        return _format_json(entries[0])
    return _format_json({"error": "无法解析属性"})


@owncloud_tool(
    "search_files",
    "按名称或路径关键字搜索文件和目录。",
    {
        "query": {
            "type": "string",
            "description": "搜索关键字，会匹配文件名和相对路径。",
        },
        "path": {
            "type": "string",
            "description": "可选，限定搜索目录。空字符串表示全盘搜索。",
        },
    },
    required=["query"],
    group="file_browse",
    short_description="Search files and folders by path keyword.",
)
def search_files(query, path=""):
    keyword = (query or "").strip().casefold()
    if not keyword:
        raise ToolExecutionError("[错误] query 不能为空")
    entries = json.loads(list_files(path=path, depth="infinity"))
    matches = []
    for entry in entries:
        haystack = f"{entry.get('path', '')} {entry.get('name', '')}".casefold()
        if keyword in haystack:
            matches.append(entry)
    return _format_json(matches)


@owncloud_tool(
    "list_shares",
    "列出当前用户可见的共享记录。",
    {
        "path": {
            "type": "string",
            "description": "可选，筛选指定路径的共享。空字符串表示列出全部。",
        },
    },
    group="sharing",
    short_description="List all shares visible to the current user.",
)
def list_shares(path=""):
    return _format_json(_list_shares_data(path=path))


@owncloud_tool(
    "get_share",
    "获取单个共享链接或共享记录的详情。",
    {
        "share_id": {
            "type": "string",
            "description": "共享记录 ID。",
        },
    },
    required=["share_id"],
    group="sharing",
    short_description="Read the details of one share record.",
)
def get_share(share_id):
    return _format_json(_get_share_data(share_id))


@owncloud_tool(
    "list_public_links",
    "列出所有公开分享链接。",
    {
        "path": {
            "type": "string",
            "description": "可选，筛选指定路径下的公开链接。",
        },
    },
    group="sharing",
    short_description="List public link shares only.",
)
def list_public_links(path=""):
    shares = [share for share in _list_shares_data(path=path) if share["share_type"] == 3]
    return _format_json(shares)


@owncloud_tool(
    "list_user_shares",
    "列出共享给内部用户的共享记录。",
    {
        "path": {
            "type": "string",
            "description": "可选，筛选指定路径下的内部共享。",
        },
    },
    group="sharing",
    short_description="List user-to-user shares only.",
)
def list_user_shares(path=""):
    shares = [share for share in _list_shares_data(path=path) if share["share_type"] == 0]
    return _format_json(shares)


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------


@owncloud_tool(
    "create_folder",
    "创建新目录。",
    {
        "path": {
            "type": "string",
            "description": "目录路径，如 'Documents/NewFolder'。",
        },
    },
    required=["path"],
    is_write=True,
    group="file_ops",
    short_description="Create a new folder at a given path.",
)
def create_folder(path):
    normalized = _normalize_path(path)
    url = _webdav_url(normalized)
    resp = _api("MKCOL", url)
    if resp.status_code == 201:
        return f"目录 {normalized} 创建成功。"
    if resp.status_code == 405:
        return f"目录 {normalized} 已存在。"
    if resp.status_code == 409:
        raise ToolExecutionError(f"[错误] 创建目录 {normalized} 失败: 父目录不存在 (409)")
    raise ToolExecutionError(f"[错误] MKCOL {normalized} -> {resp.status_code}: {resp.text[:300]}")


@owncloud_tool(
    "upload_file",
    "上传或覆盖一个文件。",
    {
        "path": {
            "type": "string",
            "description": "文件路径，如 'Documents/report.txt'。",
        },
        "content": {
            "type": "string",
            "description": "文件内容（文本）。",
        },
        "overwrite": {
            "type": "boolean",
            "description": "是否允许覆盖已存在的文件，默认 true。",
        },
    },
    required=["path", "content"],
    is_write=True,
    group="file_ops",
    short_description="Upload or overwrite a text file.",
)
def upload_file(path, content, overwrite=True):
    normalized = _normalize_path(path)
    if not overwrite:
        probe = _api("PROPFIND", _webdav_url(normalized), headers={"Depth": "0"})
        if probe.status_code == 207:
            raise ToolExecutionError(f"[错误] 文件已存在，且 overwrite=false: {normalized}")
    url = _webdav_url(normalized)
    resp = _api(
        "PUT",
        url,
        data=content.encode("utf-8"),
        headers={"Content-Type": "application/octet-stream"},
    )
    if resp.status_code in (201, 204):
        return f"文件 {normalized} 上传成功。"
    raise ToolExecutionError(f"[错误] PUT {normalized} -> {resp.status_code}: {resp.text[:300]}")


@owncloud_tool(
    "delete_path",
    "删除文件或目录（包含所有子内容）。危险操作，不可逆。",
    {
        "path": {
            "type": "string",
            "description": "要删除的文件或目录路径。",
        },
    },
    required=["path"],
    is_write=True,
    group="file_ops",
    short_description="Delete a file or folder recursively.",
)
def delete_path(path):
    normalized = _normalize_path(path)
    url = _webdav_url(normalized)
    resp = _api("DELETE", url)
    if resp.status_code == 204:
        return f"路径 {normalized} 已删除。"
    if resp.status_code == 404:
        raise ToolExecutionError(f"[错误] 路径不存在: {normalized}")
    raise ToolExecutionError(f"[错误] DELETE {normalized} -> {resp.status_code}: {resp.text[:300]}")


@owncloud_tool(
    "move_path",
    "移动或重命名文件/目录。",
    {
        "source": {
            "type": "string",
            "description": "源路径。",
        },
        "destination": {
            "type": "string",
            "description": "目标路径。",
        },
        "overwrite": {
            "type": "boolean",
            "description": "目标已存在时是否覆盖，默认 false。",
        },
    },
    required=["source", "destination"],
    is_write=True,
    group="file_ops",
    short_description="Move a file or folder to a new path.",
)
def move_path(source, destination, overwrite=False):
    normalized_source = _normalize_path(source)
    normalized_destination = _normalize_path(destination)
    src_url = _webdav_url(normalized_source)
    dst_url = _webdav_url(normalized_destination)
    resp = _api(
        "MOVE",
        src_url,
        headers={"Destination": dst_url, "Overwrite": "T" if overwrite else "F"},
    )
    if resp.status_code in (201, 204):
        return f"已将 {normalized_source} 移动到 {normalized_destination}。"
    if resp.status_code == 404:
        raise ToolExecutionError(f"[错误] 源路径不存在: {normalized_source}")
    if resp.status_code == 412:
        raise ToolExecutionError(f"[错误] 目标路径已存在: {normalized_destination}")
    raise ToolExecutionError(f"[错误] MOVE {normalized_source} -> {resp.status_code}: {resp.text[:300]}")


@owncloud_tool(
    "rename_path",
    "在当前目录内重命名文件或目录。",
    {
        "path": {
            "type": "string",
            "description": "原始文件或目录路径。",
        },
        "new_name": {
            "type": "string",
            "description": "新的文件名或目录名，不包含父目录。",
        },
        "overwrite": {
            "type": "boolean",
            "description": "目标已存在时是否覆盖，默认 false。",
        },
    },
    required=["path", "new_name"],
    is_write=True,
    group="file_ops",
    short_description="Rename a file or folder within the same parent directory.",
)
def rename_path(path, new_name, overwrite=False):
    normalized = _normalize_path(path)
    new_name = (new_name or "").strip().strip("/")
    if not new_name:
        raise ToolExecutionError("[错误] new_name 不能为空")
    destination = _join_path(_path_parent(normalized), new_name)
    return move_path(normalized, destination, overwrite=overwrite)


@owncloud_tool(
    "copy_path",
    "复制文件或目录。",
    {
        "source": {
            "type": "string",
            "description": "源路径。",
        },
        "destination": {
            "type": "string",
            "description": "目标路径。",
        },
        "overwrite": {
            "type": "boolean",
            "description": "目标已存在时是否覆盖，默认 false。",
        },
    },
    required=["source", "destination"],
    is_write=True,
    group="file_ops",
    short_description="Copy a file or folder to a new path.",
)
def copy_path(source, destination, overwrite=False):
    normalized_source = _normalize_path(source)
    normalized_destination = _normalize_path(destination)
    src_url = _webdav_url(normalized_source)
    dst_url = _webdav_url(normalized_destination)
    resp = _api(
        "COPY",
        src_url,
        headers={"Destination": dst_url, "Overwrite": "T" if overwrite else "F"},
    )
    if resp.status_code in (201, 204):
        return f"已将 {normalized_source} 复制到 {normalized_destination}。"
    if resp.status_code == 404:
        raise ToolExecutionError(f"[错误] 源路径不存在: {normalized_source}")
    if resp.status_code == 412:
        raise ToolExecutionError(f"[错误] 目标路径已存在: {normalized_destination}")
    raise ToolExecutionError(f"[错误] COPY {normalized_source} -> {resp.status_code}: {resp.text[:300]}")


@owncloud_tool(
    "create_public_link",
    "为文件或目录创建公开分享链接。",
    {
        "path": {
            "type": "string",
            "description": "要共享的文件或目录路径。",
        },
        "name": {
            "type": "string",
            "description": "共享链接名称（可选）。",
        },
        "permissions": {
            "type": "integer",
            "description": "权限位，例如 1=只读，15=全部。",
        },
        "password": {
            "type": "string",
            "description": "可选，公开链接密码。",
        },
        "expire_date": {
            "type": "string",
            "description": "可选，到期日期，格式 YYYY-MM-DD。",
        },
    },
    required=["path"],
    is_write=True,
    group="sharing",
    short_description="Create a public link share for a file or folder.",
)
def create_public_link(path, name="", permissions=1, password="", expire_date=""):
    normalized = _normalize_path(path)
    url = _ocs_url("ocs/v2.php/apps/files_sharing/api/v1/shares")
    effective_password = password or _config["default_public_link_password"]
    payload = _create_share_payload(
        path=normalized,
        share_type=3,
        name=name or normalized.split("/")[-1],
        permissions=permissions,
        password=effective_password,
        expire_date=expire_date,
    )
    resp = _api("POST", url, headers=_ocs_headers(), data=payload)
    if resp.status_code not in (200, 201):
        raise ToolExecutionError(f"[错误] 创建公开链接失败: {resp.status_code}: {resp.text[:300]}")
    share_data = _extract_ocs_data(resp, "创建公开链接")
    return _format_json(_normalize_share(share_data))


@owncloud_tool(
    "create_share",
    "兼容旧任务的公开链接创建工具。",
    {
        "path": {
            "type": "string",
            "description": "要共享的文件或目录路径。",
        },
        "name": {
            "type": "string",
            "description": "共享链接名称（可选）。",
        },
        "permissions": {
            "type": "integer",
            "description": "权限位，例如 1=只读，15=全部。",
        },
        "password": {
            "type": "string",
            "description": "可选，公开链接密码。",
        },
        "expire_date": {
            "type": "string",
            "description": "可选，到期日期，格式 YYYY-MM-DD。",
        },
    },
    required=["path"],
    is_write=True,
    group="sharing",
    short_description="Compatibility alias for creating a public link share.",
)
def create_share(path, name="", permissions=1, password="", expire_date=""):
    return create_public_link(path=path, name=name, permissions=permissions, password=password, expire_date=expire_date)


@owncloud_tool(
    "create_user_share",
    "将文件或目录共享给内部用户。",
    {
        "path": {
            "type": "string",
            "description": "要共享的文件或目录路径。",
        },
        "share_with": {
            "type": "string",
            "description": "目标用户名。",
        },
        "permissions": {
            "type": "integer",
            "description": "权限位，例如 1=只读，15=全部。",
        },
    },
    required=["path", "share_with"],
    is_write=True,
    group="sharing",
    short_description="Create an internal user-to-user share.",
)
def create_user_share(path, share_with, permissions=1):
    normalized = _normalize_path(path)
    url = _ocs_url("ocs/v2.php/apps/files_sharing/api/v1/shares")
    payload = _create_share_payload(
        path=normalized,
        share_type=0,
        permissions=permissions,
        share_with=share_with,
    )
    resp = _api("POST", url, headers=_ocs_headers(), data=payload)
    if resp.status_code not in (200, 201):
        raise ToolExecutionError(f"[错误] 创建内部共享失败: {resp.status_code}: {resp.text[:300]}")
    share_data = _extract_ocs_data(resp, "创建内部共享")
    return _format_json(_normalize_share(share_data))


@owncloud_tool(
    "update_share_permissions",
    "更新共享记录的权限。",
    {
        "share_id": {
            "type": "string",
            "description": "共享记录 ID。",
        },
        "permissions": {
            "type": "integer",
            "description": "新的权限位，例如 1=只读，15=全部。",
        },
    },
    required=["share_id", "permissions"],
    is_write=True,
    group="sharing",
    short_description="Change the permissions on an existing share.",
)
def update_share_permissions(share_id, permissions):
    url = _ocs_url(f"ocs/v2.php/apps/files_sharing/api/v1/shares/{share_id}")
    resp = _api("PUT", url, headers=_ocs_headers(), data={"permissions": str(permissions)})
    if resp.status_code not in (200, 201):
        raise ToolExecutionError(f"[错误] 更新共享权限失败: {resp.status_code}: {resp.text[:300]}")
    return _format_json(_get_share_data(share_id))


@owncloud_tool(
    "delete_share",
    "删除一个共享链接或共享记录。",
    {
        "share_id": {
            "type": "string",
            "description": "共享记录 ID。",
        },
    },
    required=["share_id"],
    is_write=True,
    group="sharing",
    short_description="Delete one existing share record.",
)
def delete_share(share_id):
    url = _ocs_url(f"ocs/v2.php/apps/files_sharing/api/v1/shares/{share_id}")
    resp = _api("DELETE", url, headers=_ocs_headers())
    if resp.status_code in (200, 204):
        return f"共享链接 {share_id} 已删除。"
    if resp.status_code == 404:
        raise ToolExecutionError(f"[错误] 共享链接不存在: {share_id}")
    raise ToolExecutionError(f"[错误] 删除共享失败: {resp.status_code}: {resp.text[:300]}")
