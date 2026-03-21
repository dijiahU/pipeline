"""
GitLab API 工具注册 — 服务化工具架构标准

本模块不依赖沙箱，直接通过 HTTP 调用 GitLab API v4。
以后所有服务工具（RocketChat、文件系统等）都按此模式接入。

公共接口（给 environment.py 调用）:
  get_all_schemas() -> list
  call_tool(name, args) -> str
  get_tool_names() -> list
"""

import json
import os
import urllib.parse

from .exceptions import ToolExecutionError

try:
    import requests
except ModuleNotFoundError:
    requests = None

# ==================== 连接配置 ====================

_config = {
    "base_url": os.environ.get("GITLAB_BASE_URL", "http://localhost:8929"),
    "token": os.environ.get("GITLAB_ACCESS_TOKEN", "root-token"),
}

# ==================== 工具注册 ====================

_REGISTRY = {}  # name -> {"handler": fn, "schema": dict}


def gitlab_tool(name, description, params, required=None):
    """装饰器：注册 GitLab 工具到 _REGISTRY"""
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

        _REGISTRY[name] = {
            "handler": func,
            "schema": {
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
            },
        }
        return func
    return decorator


# ==================== 公共接口 ====================


def get_all_schemas():
    """获取所有 tool 的 OpenAI function calling schema"""
    return [t["schema"] for t in _REGISTRY.values()]


def call_tool(name, args):
    """按名称动态调用 tool"""
    t = _REGISTRY.get(name)
    if not t:
        raise ToolExecutionError(f"[错误] 未知 tool: {name}")
    try:
        return t["handler"](**args)
    except ToolExecutionError:
        raise
    except Exception as exc:
        raise ToolExecutionError(f"[执行出错] {type(exc).__name__}: {exc}") from exc


def get_tool_names():
    """获取所有已注册的 tool 名称"""
    return list(_REGISTRY.keys())


# ==================== 内部 API 封装 ====================


def _require_requests():
    if requests is None:
        raise ToolExecutionError("requests 库未安装，无法调用 GitLab API。pip install requests")


def _api(method, path, **kwargs):
    """统一 GitLab API v4 请求"""
    _require_requests()
    url = f"{_config['base_url']}/api/v4/{path.lstrip('/')}"
    headers = {"PRIVATE-TOKEN": _config["token"]}
    try:
        return requests.request(method, url, headers=headers, timeout=30, **kwargs)
    except requests.RequestException as exc:
        raise ToolExecutionError(f"[GitLab 请求失败] {type(exc).__name__}: {exc}") from exc


def _api_json(method, path, **kwargs):
    """发起请求并返回 JSON，出错时抛异常"""
    resp = _api(method, path, **kwargs)
    if resp.status_code >= 400:
        raise ToolExecutionError(f"[GitLab API 错误] {resp.status_code}: {resp.text[:500]}")
    try:
        return resp.json()
    except Exception:
        return resp.text[:1000]


def _format_json(data):
    """将数据格式化为可读字符串"""
    if isinstance(data, str):
        return data
    return json.dumps(data, ensure_ascii=False, indent=2)


def _encode_path(path):
    """URL 编码文件路径"""
    return urllib.parse.quote(path, safe="")


def _project_ref(project_id):
    return _encode_path(project_id) if "/" in str(project_id) else project_id


# ==================== 只读工具（低风险）— 8 个 ====================


@gitlab_tool(
    "list_projects",
    "列出 GitLab 上所有可见项目。",
    {
        "per_page": {
            "type": "integer",
            "description": "每页返回数量，默认 20，最大 100",
        },
    },
)
def list_projects(per_page=20):
    data = _api_json("GET", "projects", params={"per_page": per_page, "order_by": "name"})
    if isinstance(data, str):
        return data
    results = []
    for p in data:
        results.append({
            "id": p["id"],
            "name": p["name"],
            "path_with_namespace": p.get("path_with_namespace", ""),
            "description": (p.get("description") or "")[:200],
            "default_branch": p.get("default_branch", ""),
        })
    return _format_json(results)


@gitlab_tool(
    "get_project",
    "获取指定 GitLab 项目的详细信息。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或 URL 编码的路径（如 'root/sotopia'）",
        },
    },
)
def get_project(project_id):
    encoded = _project_ref(project_id)
    data = _api_json("GET", f"projects/{encoded}")
    return _format_json(data)


@gitlab_tool(
    "list_branches",
    "列出指定项目的所有分支。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "per_page": {
            "type": "integer",
            "description": "每页返回数量，默认 20",
        },
    },
)
def list_branches(project_id, per_page=20):
    encoded = _project_ref(project_id)
    data = _api_json("GET", f"projects/{encoded}/repository/branches", params={"per_page": per_page})
    results = []
    for b in data:
        results.append({
            "name": b["name"],
            "protected": b.get("protected", False),
            "merged": b.get("merged", False),
            "default": b.get("default", False),
        })
    return _format_json(results)


@gitlab_tool(
    "list_issues",
    "列出指定项目的 issue。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "state": {
            "type": "string",
            "enum": ["opened", "closed", "all"],
            "description": "issue 状态筛选，默认 opened",
        },
        "per_page": {
            "type": "integer",
            "description": "每页返回数量，默认 20",
        },
    },
)
def list_issues(project_id, state="opened", per_page=20):
    encoded = _project_ref(project_id)
    data = _api_json("GET", f"projects/{encoded}/issues", params={"state": state, "per_page": per_page})
    results = []
    for issue in data:
        results.append({
            "iid": issue["iid"],
            "title": issue["title"],
            "state": issue["state"],
            "labels": issue.get("labels", []),
            "assignee": (issue.get("assignee") or {}).get("username", ""),
            "created_at": issue.get("created_at", ""),
        })
    return _format_json(results)


@gitlab_tool(
    "list_merge_requests",
    "列出指定项目的 Merge Request。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "state": {
            "type": "string",
            "enum": ["opened", "closed", "merged", "all"],
            "description": "MR 状态筛选，默认 opened",
        },
        "per_page": {
            "type": "integer",
            "description": "每页返回数量，默认 20",
        },
    },
)
def list_merge_requests(project_id, state="opened", per_page=20):
    encoded = _project_ref(project_id)
    data = _api_json(
        "GET", f"projects/{encoded}/merge_requests",
        params={"state": state, "per_page": per_page},
    )
    results = []
    for mr in data:
        results.append({
            "iid": mr["iid"],
            "title": mr["title"],
            "state": mr["state"],
            "source_branch": mr.get("source_branch", ""),
            "target_branch": mr.get("target_branch", ""),
            "author": (mr.get("author") or {}).get("username", ""),
        })
    return _format_json(results)


@gitlab_tool(
    "read_repo_file",
    "读取仓库中指定分支上的文件内容。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "file_path": {
            "type": "string",
            "description": "文件在仓库中的路径（如 'src/main.py'）",
        },
        "ref": {
            "type": "string",
            "description": "分支名或 commit SHA，默认 main",
        },
    },
)
def read_repo_file(project_id, file_path, ref="main"):
    encoded_proj = _project_ref(project_id)
    encoded_file = _encode_path(file_path)
    resp = _api("GET", f"projects/{encoded_proj}/repository/files/{encoded_file}/raw", params={"ref": ref})
    if resp.status_code >= 400:
        raise ToolExecutionError(f"[GitLab API 错误] {resp.status_code}: {resp.text[:500]}")
    return resp.text[:5000]


@gitlab_tool(
    "read_pipeline_log",
    "读取 CI/CD job 的执行日志。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "job_id": {
            "type": "string",
            "description": "Job ID",
        },
    },
)
def read_pipeline_log(project_id, job_id):
    encoded = _project_ref(project_id)
    resp = _api("GET", f"projects/{encoded}/jobs/{job_id}/trace")
    if resp.status_code >= 400:
        raise ToolExecutionError(f"[GitLab API 错误] {resp.status_code}: {resp.text[:500]}")
    return resp.text[:5000]


@gitlab_tool(
    "get_branch_protection",
    "查看指定分支的保护规则。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "branch_name": {
            "type": "string",
            "description": "分支名称",
        },
    },
)
def get_branch_protection(project_id, branch_name):
    encoded_proj = _project_ref(project_id)
    encoded_branch = _encode_path(branch_name)
    data = _api_json("GET", f"projects/{encoded_proj}/protected_branches/{encoded_branch}")
    return _format_json(data)


# ==================== 预览工具（中风险）— 2 个 ====================


@gitlab_tool(
    "preview_delete_branches",
    "预览删除操作：列出匹配的分支及其 protected/MR 状态，不执行实际删除。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "branch_pattern": {
            "type": "string",
            "description": "分支名匹配模式（支持 * 通配符，如 'feature-*'）",
        },
    },
)
def preview_delete_branches(project_id, branch_pattern):
    import fnmatch

    encoded = _project_ref(project_id)

    # 获取所有分支
    branches_data = _api_json("GET", f"projects/{encoded}/repository/branches", params={"per_page": 100})

    # 获取活跃 MR
    mrs_data = _api_json("GET", f"projects/{encoded}/merge_requests", params={"state": "opened", "per_page": 100})
    mr_branches = set()
    if isinstance(mrs_data, list):
        for mr in mrs_data:
            mr_branches.add(mr.get("source_branch", ""))

    matched = []
    for b in branches_data:
        name = b["name"]
        if not fnmatch.fnmatch(name, branch_pattern):
            continue
        matched.append({
            "name": name,
            "protected": b.get("protected", False),
            "default": b.get("default", False),
            "merged": b.get("merged", False),
            "has_active_mr": name in mr_branches,
        })

    protected_count = sum(1 for b in matched if b["protected"])
    mr_count = sum(1 for b in matched if b["has_active_mr"])
    default_count = sum(1 for b in matched if b["default"])

    return _format_json({
        "pattern": branch_pattern,
        "total_matched": len(matched),
        "protected_count": protected_count,
        "default_branch_count": default_count,
        "active_mr_count": mr_count,
        "branches": matched,
        "warnings": [
            w for w in [
                f"包含 {protected_count} 个受保护分支" if protected_count else None,
                f"包含 {default_count} 个默认分支" if default_count else None,
                f"包含 {mr_count} 个有活跃 MR 的分支" if mr_count else None,
            ] if w
        ],
    })


@gitlab_tool(
    "preview_close_issues",
    "预览关闭操作：列出匹配的 issue 及其 labels/assignee，不执行实际关闭。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "label_filter": {
            "type": "string",
            "description": "按 label 过滤（可选，逗号分隔多个 label）",
        },
    },
)
def preview_close_issues(project_id, label_filter=""):
    encoded = _project_ref(project_id)

    params = {"state": "opened", "per_page": 100}
    if label_filter:
        params["labels"] = label_filter

    data = _api_json("GET", f"projects/{encoded}/issues", params=params)

    matched = []
    for issue in data:
        matched.append({
            "iid": issue["iid"],
            "title": issue["title"],
            "labels": issue.get("labels", []),
            "assignee": (issue.get("assignee") or {}).get("username", ""),
            "created_at": issue.get("created_at", ""),
        })

    return _format_json({
        "total_matched": len(matched),
        "label_filter": label_filter or "(无筛选)",
        "issues": matched,
    })


@gitlab_tool(
    "preview_close_issue",
    "预览关闭单个 issue：查看目标 issue 当前状态，不执行实际关闭。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "issue_iid": {
            "type": "integer",
            "description": "Issue 的项目内编号（iid）",
        },
    },
)
def preview_close_issue(project_id, issue_iid):
    encoded = _project_ref(project_id)
    data = _api_json("GET", f"projects/{encoded}/issues/{issue_iid}")
    issue_view = {
        "iid": data["iid"],
        "title": data["title"],
        "state": data["state"],
        "labels": data.get("labels", []),
        "assignee": (data.get("assignee") or {}).get("username", ""),
        "created_at": data.get("created_at", ""),
    }
    return _format_json({
        "total_matched": 1,
        "issues": [issue_view],
        "warnings": ["该预览针对单个 issue，不会批量关闭其他 issue。"],
    })


@gitlab_tool(
    "preview_update_branch_protection",
    "预览分支保护规则变更：查看分支当前保护状态，不执行实际修改。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "branch_name": {
            "type": "string",
            "description": "分支名称",
        },
        "action": {
            "type": "string",
            "enum": ["protect", "unprotect"],
            "description": "目标操作",
        },
    },
)
def preview_update_branch_protection(project_id, branch_name, action):
    encoded_proj = _project_ref(project_id)
    encoded_branch = _encode_path(branch_name)
    resp = _api("GET", f"projects/{encoded_proj}/protected_branches/{encoded_branch}")
    currently_protected = resp.status_code == 200
    if resp.status_code not in (200, 404):
        raise ToolExecutionError(f"[GitLab API 错误] {resp.status_code}: {resp.text[:500]}")

    warnings = []
    if action == "protect" and currently_protected:
        warnings.append("目标分支已经受保护")
    if action == "unprotect" and not currently_protected:
        warnings.append("目标分支当前未受保护")

    return _format_json({
        "total_matched": 1,
        "branches": [{
            "name": branch_name,
            "currently_protected": currently_protected,
            "requested_action": action,
        }],
        "warnings": warnings,
    })


# ==================== 写操作工具（高风险）— 3 个 ====================


@gitlab_tool(
    "delete_branch",
    "删除指定项目中的单个分支。危险操作，不可逆。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "branch_name": {
            "type": "string",
            "description": "要删除的分支名称",
        },
    },
)
def delete_branch(project_id, branch_name):
    encoded_proj = _project_ref(project_id)
    encoded_branch = _encode_path(branch_name)
    resp = _api("DELETE", f"projects/{encoded_proj}/repository/branches/{encoded_branch}")
    if resp.status_code == 204:
        return f"分支 '{branch_name}' 已删除。"
    if resp.status_code >= 400:
        raise ToolExecutionError(f"[GitLab API 错误] {resp.status_code}: {resp.text[:500]}")
    return f"分支 '{branch_name}' 删除请求已发送，状态码: {resp.status_code}"


@gitlab_tool(
    "close_issue",
    "关闭指定项目中的单个 issue。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "issue_iid": {
            "type": "integer",
            "description": "Issue 的项目内编号（iid）",
        },
    },
)
def close_issue(project_id, issue_iid):
    encoded = _project_ref(project_id)
    resp = _api("PUT", f"projects/{encoded}/issues/{issue_iid}", json={"state_event": "close"})
    if resp.status_code >= 400:
        raise ToolExecutionError(f"[GitLab API 错误] {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    return f"Issue #{issue_iid} ('{data.get('title', '')}') 已关闭。"


@gitlab_tool(
    "update_branch_protection",
    "修改分支保护规则：添加或移除保护。危险操作，可能影响 CI/CD 流程和代码合并策略。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "branch_name": {
            "type": "string",
            "description": "分支名称",
        },
        "action": {
            "type": "string",
            "enum": ["protect", "unprotect"],
            "description": "protect=添加保护, unprotect=移除保护",
        },
    },
)
def update_branch_protection(project_id, branch_name, action):
    encoded_proj = _project_ref(project_id)
    encoded_branch = _encode_path(branch_name)

    if action == "protect":
        resp = _api("POST", f"projects/{encoded_proj}/protected_branches", json={"name": branch_name})
        if resp.status_code >= 400:
            raise ToolExecutionError(f"[GitLab API 错误] {resp.status_code}: {resp.text[:500]}")
        return f"分支 '{branch_name}' 已添加保护。"
    if action == "unprotect":
        resp = _api("DELETE", f"projects/{encoded_proj}/protected_branches/{encoded_branch}")
        if resp.status_code == 204:
            return f"分支 '{branch_name}' 保护已移除。"
        if resp.status_code >= 400:
            raise ToolExecutionError(f"[GitLab API 错误] {resp.status_code}: {resp.text[:500]}")
        return f"分支 '{branch_name}' 保护移除请求已发送，状态码: {resp.status_code}"
    raise ToolExecutionError(f"[错误] action 必须是 'protect' 或 'unprotect'，收到: {action}")
