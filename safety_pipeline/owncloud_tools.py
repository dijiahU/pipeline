"""
ownCloud (oCIS) WebDAV + OCS 工具注册 — 服务化工具架构标准

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
}

_REGISTRY = ServiceToolRegistry(service_id="owncloud")


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


def _webdav_url(path=""):
    user = _config["admin_user"]
    return f"{_config['base_url']}/dav/files/{user}/{path.lstrip('/')}"


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


def _parse_propfind_entries(xml_text):
    """Parse PROPFIND XML response into a list of file/folder dicts."""
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
        entries.append({
            "href": href,
            "name": display_name or href.rstrip("/").split("/")[-1],
            "type": "directory" if is_dir else "file",
            "size": int(size) if size else 0,
            "last_modified": last_modified,
        })
    return entries


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------

@owncloud_tool(
    "list_files",
    "列出指定目录下的文件和子目录。必须提供 path 参数。",
    {
        "path": {
            "type": "string",
            "description": "目录路径，如 'Documents' 或 'Projects/Website-Redesign'。'/' 或空字符串表示根目录。",
        },
        "depth": {
            "type": "string",
            "description": "列出深度：'1' 仅当前层（默认），'infinity' 递归列出所有。",
        },
    },
    required=["path"],
)
def list_files(path="", depth="1"):
    url = _webdav_url(path)
    resp = _api("PROPFIND", url, headers={"Depth": depth})
    if resp.status_code == 404:
        raise ToolExecutionError(f"[错误] 路径不存在: {path}")
    if resp.status_code not in (207,):
        raise ToolExecutionError(f"[错误] PROPFIND {path} -> {resp.status_code}: {resp.text[:300]}")
    entries = _parse_propfind_entries(resp.text)
    # Skip the first entry (the directory itself)
    if entries and entries[0]["href"].rstrip("/").endswith(path.rstrip("/") or _config["admin_user"]):
        entries = entries[1:]
    return _format_json(entries)


@owncloud_tool(
    "read_file",
    "读取指定文件的内容。",
    {
        "path": {
            "type": "string",
            "description": "文件路径，如 'Documents/README.md'",
        },
    },
)
def read_file(path):
    url = _webdav_url(path)
    resp = _api("GET", url)
    if resp.status_code == 404:
        raise ToolExecutionError(f"[错误] 文件不存在: {path}")
    if resp.status_code != 200:
        raise ToolExecutionError(f"[错误] GET {path} -> {resp.status_code}: {resp.text[:300]}")
    return resp.text


@owncloud_tool(
    "file_info",
    "获取文件或目录的详细属性信息。",
    {
        "path": {
            "type": "string",
            "description": "文件或目录路径",
        },
    },
)
def file_info(path):
    url = _webdav_url(path)
    resp = _api("PROPFIND", url, headers={"Depth": "0"})
    if resp.status_code == 404:
        raise ToolExecutionError(f"[错误] 路径不存在: {path}")
    if resp.status_code not in (207,):
        raise ToolExecutionError(f"[错误] PROPFIND {path} -> {resp.status_code}: {resp.text[:300]}")
    entries = _parse_propfind_entries(resp.text)
    if entries:
        return _format_json(entries[0])
    return _format_json({"error": "无法解析属性"})


@owncloud_tool(
    "list_shares",
    "列出当前用户的所有共享链接。",
    {
        "path": {
            "type": "string",
            "description": "可选，筛选指定路径的共享。空字符串表示列出全部。",
        },
    },
)
def list_shares(path=""):
    url = _ocs_url("ocs/v2.php/apps/files_sharing/api/v1/shares")
    params = {"format": "json"}
    if path:
        params["path"] = f"/{path.lstrip('/')}"
    resp = _api("GET", url, params=params, headers={"OCS-APIREQUEST": "true"})
    if resp.status_code != 200:
        raise ToolExecutionError(f"[错误] 获取共享列表失败: {resp.status_code}: {resp.text[:300]}")
    try:
        data = resp.json()
        shares = data.get("ocs", {}).get("data", [])
        results = []
        for s in shares:
            results.append({
                "id": s.get("id"),
                "path": s.get("path"),
                "share_type": s.get("share_type"),
                "url": s.get("url", ""),
                "permissions": s.get("permissions"),
                "name": s.get("name", ""),
            })
        return _format_json(results)
    except Exception:
        return resp.text[:1000]


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------

@owncloud_tool(
    "create_folder",
    "创建新目录。",
    {
        "path": {
            "type": "string",
            "description": "目录路径，如 'Documents/NewFolder'",
        },
    },
    is_write=True,
)
def create_folder(path):
    url = _webdav_url(path)
    resp = _api("MKCOL", url)
    if resp.status_code == 201:
        return f"目录 {path} 创建成功。"
    if resp.status_code == 405:
        return f"目录 {path} 已存在。"
    if resp.status_code == 409:
        raise ToolExecutionError(f"[错误] 创建目录 {path} 失败: 父目录不存在 (409)")
    raise ToolExecutionError(f"[错误] MKCOL {path} -> {resp.status_code}: {resp.text[:300]}")


@owncloud_tool(
    "upload_file",
    "上传或覆盖一个文件。",
    {
        "path": {
            "type": "string",
            "description": "文件路径，如 'Documents/report.txt'",
        },
        "content": {
            "type": "string",
            "description": "文件内容（文本）",
        },
    },
    is_write=True,
)
def upload_file(path, content):
    url = _webdav_url(path)
    resp = _api("PUT", url, data=content.encode("utf-8"),
                headers={"Content-Type": "application/octet-stream"})
    if resp.status_code in (201, 204):
        return f"文件 {path} 上传成功。"
    raise ToolExecutionError(f"[错误] PUT {path} -> {resp.status_code}: {resp.text[:300]}")


@owncloud_tool(
    "delete_path",
    "删除文件或目录（包含所有子内容）。危险操作，不可逆。",
    {
        "path": {
            "type": "string",
            "description": "要删除的文件或目录路径",
        },
    },
    is_write=True,
)
def delete_path(path):
    url = _webdav_url(path)
    resp = _api("DELETE", url)
    if resp.status_code == 204:
        return f"路径 {path} 已删除。"
    if resp.status_code == 404:
        raise ToolExecutionError(f"[错误] 路径不存在: {path}")
    raise ToolExecutionError(f"[错误] DELETE {path} -> {resp.status_code}: {resp.text[:300]}")


@owncloud_tool(
    "move_path",
    "移动或重命名文件/目录。",
    {
        "source": {
            "type": "string",
            "description": "源路径",
        },
        "destination": {
            "type": "string",
            "description": "目标路径",
        },
    },
    is_write=True,
)
def move_path(source, destination):
    src_url = _webdav_url(source)
    dst_url = _webdav_url(destination)
    resp = _api("MOVE", src_url, headers={"Destination": dst_url, "Overwrite": "F"})
    if resp.status_code in (201, 204):
        return f"已将 {source} 移动到 {destination}。"
    if resp.status_code == 404:
        raise ToolExecutionError(f"[错误] 源路径不存在: {source}")
    if resp.status_code == 412:
        raise ToolExecutionError(f"[错误] 目标路径已存在: {destination}")
    raise ToolExecutionError(f"[错误] MOVE {source} -> {resp.status_code}: {resp.text[:300]}")


@owncloud_tool(
    "copy_path",
    "复制文件或目录。",
    {
        "source": {
            "type": "string",
            "description": "源路径",
        },
        "destination": {
            "type": "string",
            "description": "目标路径",
        },
    },
    is_write=True,
)
def copy_path(source, destination):
    src_url = _webdav_url(source)
    dst_url = _webdav_url(destination)
    resp = _api("COPY", src_url, headers={"Destination": dst_url, "Overwrite": "F"})
    if resp.status_code in (201, 204):
        return f"已将 {source} 复制到 {destination}。"
    if resp.status_code == 404:
        raise ToolExecutionError(f"[错误] 源路径不存在: {source}")
    if resp.status_code == 412:
        raise ToolExecutionError(f"[错误] 目标路径已存在: {destination}")
    raise ToolExecutionError(f"[错误] COPY {source} -> {resp.status_code}: {resp.text[:300]}")


@owncloud_tool(
    "create_share",
    "为文件或目录创建公开共享链接。",
    {
        "path": {
            "type": "string",
            "description": "要共享的文件或目录路径",
        },
        "name": {
            "type": "string",
            "description": "共享链接名称（可选）",
        },
        "permissions": {
            "type": "integer",
            "description": "权限：1=读取（默认），2=更新，4=创建，15=全部",
        },
    },
    is_write=True,
)
def create_share(path, name="", permissions=1):
    url = _ocs_url("ocs/v2.php/apps/files_sharing/api/v1/shares")
    resp = _api(
        "POST", url,
        headers={"OCS-APIREQUEST": "true", "Content-Type": "application/json"},
        json={
            "path": f"/{path.lstrip('/')}",
            "shareType": 3,  # public link
            "permissions": permissions,
            "name": name or path.split("/")[-1],
        },
    )
    if resp.status_code in (200, 201):
        try:
            share_data = resp.json().get("ocs", {}).get("data", {})
            return _format_json({
                "id": share_data.get("id"),
                "url": share_data.get("url", ""),
                "path": path,
                "permissions": permissions,
            })
        except Exception:
            return f"共享链接创建成功: {path}"
    raise ToolExecutionError(f"[错误] 创建共享失败: {resp.status_code}: {resp.text[:300]}")


@owncloud_tool(
    "delete_share",
    "删除一个共享链接。危险操作。",
    {
        "share_id": {
            "type": "string",
            "description": "共享链接 ID",
        },
    },
    is_write=True,
)
def delete_share(share_id):
    url = _ocs_url(f"ocs/v2.php/apps/files_sharing/api/v1/shares/{share_id}")
    resp = _api("DELETE", url, headers={"OCS-APIREQUEST": "true"})
    if resp.status_code in (200, 204):
        return f"共享链接 {share_id} 已删除。"
    if resp.status_code == 404:
        raise ToolExecutionError(f"[错误] 共享链接不存在: {share_id}")
    raise ToolExecutionError(f"[错误] 删除共享失败: {resp.status_code}: {resp.text[:300]}")
