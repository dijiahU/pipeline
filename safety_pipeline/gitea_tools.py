"""
Gitea API 工具注册 — 服务化工具架构标准

本模块直接通过 HTTP 调用 Gitea API v1。
公共接口（给 environment.py 调用）:
  get_all_schemas() -> list
  call_tool(name, args) -> str
  get_tool_names() -> list
"""

import base64
import json
import os
import urllib.parse

from .exceptions import ToolExecutionError

try:
    import requests
except ModuleNotFoundError:
    requests = None


_config = {
    "base_url": os.environ.get("GITEA_BASE_URL", "http://localhost:3000"),
    "token": os.environ.get("GITEA_ACCESS_TOKEN", "root-token"),
    "owner": os.environ.get("GITEA_OWNER", "root"),
}


_REGISTRY = {}


def gitea_tool(name, description, params, required=None):
    """装饰器：注册 Gitea 工具到 _REGISTRY"""

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


def get_all_schemas():
    return [t["schema"] for t in _REGISTRY.values()]


def call_tool(name, args):
    tool = _REGISTRY.get(name)
    if not tool:
        raise ToolExecutionError(f"[错误] 未知 tool: {name}")
    try:
        return tool["handler"](**args)
    except ToolExecutionError:
        raise
    except Exception as exc:
        raise ToolExecutionError(f"[执行出错] {type(exc).__name__}: {exc}") from exc


def get_tool_names():
    return list(_REGISTRY.keys())


def _require_requests():
    if requests is None:
        raise ToolExecutionError("requests 库未安装，无法调用 Gitea API。pip install requests")


def _api(method, path, **kwargs):
    _require_requests()
    url = f"{_config['base_url'].rstrip('/')}/api/v1/{path.lstrip('/')}"
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"token {_config['token']}"
    try:
        return requests.request(method, url, headers=headers, timeout=30, **kwargs)
    except requests.RequestException as exc:
        raise ToolExecutionError(f"[Gitea 请求失败] {type(exc).__name__}: {exc}") from exc


def _api_json(method, path, **kwargs):
    resp = _api(method, path, **kwargs)
    if resp.status_code >= 400:
        raise ToolExecutionError(f"[Gitea API 错误] {resp.status_code}: {resp.text[:500]}")
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


def _encode_path(path):
    return urllib.parse.quote(str(path), safe="")


def _list_projects_raw(limit=100):
    data = _api_json(
        "GET",
        "repos/search",
        params={"limit": limit, "sort": "alpha", "order": "asc"},
    )
    if isinstance(data, dict):
        return data.get("data", [])
    return data if isinstance(data, list) else []


def _project_ref(project_id):
    value = str(project_id).strip()
    if not value:
        raise ToolExecutionError("[错误] project_id 不能为空")

    if "/" in value:
        owner, repo = value.split("/", 1)
        return owner, repo

    if value.isdigit():
        repo_id = int(value)
        for repo in _list_projects_raw(limit=200):
            if int(repo.get("id", -1)) == repo_id:
                full_name = repo.get("full_name", "")
                if "/" in full_name:
                    owner, repo_name = full_name.split("/", 1)
                    return owner, repo_name
                return (repo.get("owner") or {}).get("login", _config["owner"]), repo.get("name", value)
        raise ToolExecutionError(f"[错误] 找不到 id={project_id} 的 Gitea 仓库")

    return _config["owner"], value


def _repo_meta(project_id):
    owner, repo = _project_ref(project_id)
    return _api_json("GET", f"repos/{owner}/{repo}")


def _branch_protection_map(owner, repo):
    resp = _api("GET", f"repos/{owner}/{repo}/branch_protections")
    if resp.status_code == 404:
        return {}
    if resp.status_code >= 400:
        raise ToolExecutionError(f"[Gitea API 错误] {resp.status_code}: {resp.text[:500]}")
    protections = resp.json() if resp.text else []
    return {
        item.get("branch_name") or item.get("rule_name"): item
        for item in protections
        if isinstance(item, dict)
    }


def _normalize_issue_state_for_gitea(state):
    mapping = {"opened": "open", "closed": "closed", "all": "all", "open": "open"}
    return mapping.get(state, state)


def _normalize_issue_state_from_gitea(state):
    if state == "open":
        return "opened"
    return state


def _normalize_labels(labels):
    normalized = []
    for label in labels or []:
        if isinstance(label, dict):
            normalized.append(label.get("name", ""))
        else:
            normalized.append(str(label))
    return [item for item in normalized if item]


def _read_contents(owner, repo, file_path, ref="main"):
    encoded_file = "/".join(_encode_path(part) for part in str(file_path).split("/"))
    data = _api_json("GET", f"repos/{owner}/{repo}/contents/{encoded_file}", params={"ref": ref})
    if isinstance(data, list):
        raise ToolExecutionError(f"[错误] {file_path} 是目录，不是文件")
    return data


@gitea_tool(
    "list_projects",
    "列出 Gitea 上所有可见项目。",
    {
        "per_page": {
            "type": "integer",
            "description": "每页返回数量，默认 20，最大 100",
        },
    },
)
def list_projects(per_page=20):
    repos = _list_projects_raw(limit=per_page)
    results = []
    for repo in repos:
        results.append(
            {
                "id": repo["id"],
                "name": repo["name"],
                "path_with_namespace": repo.get("full_name", ""),
                "description": (repo.get("description") or "")[:200],
                "default_branch": repo.get("default_branch", ""),
            }
        )
    return _format_json(results)


@gitea_tool(
    "get_project",
    "获取指定 Gitea 项目的详细信息。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID、仓库名，或路径（如 'root/openclaw'）",
        },
    },
)
def get_project(project_id):
    data = _repo_meta(project_id)
    return _format_json(data)


@gitea_tool(
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
    owner, repo = _project_ref(project_id)
    repo_meta = _api_json("GET", f"repos/{owner}/{repo}")
    protections = _branch_protection_map(owner, repo)
    default_branch = repo_meta.get("default_branch", "")
    data = _api_json("GET", f"repos/{owner}/{repo}/branches", params={"limit": per_page})
    results = []
    for branch in data:
        name = branch["name"]
        results.append(
            {
                "name": name,
                "protected": name in protections or bool(branch.get("effective_branch_protection_name")),
                "merged": False,
                "default": name == default_branch,
            }
        )
    return _format_json(results)


@gitea_tool(
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
    owner, repo = _project_ref(project_id)
    data = _api_json(
        "GET",
        f"repos/{owner}/{repo}/issues",
        params={
            "state": _normalize_issue_state_for_gitea(state),
            "type": "issues",
            "limit": per_page,
        },
    )
    results = []
    for issue in data:
        results.append(
            {
                "iid": issue["number"],
                "title": issue["title"],
                "state": _normalize_issue_state_from_gitea(issue.get("state", "")),
                "labels": _normalize_labels(issue.get("labels", [])),
                "assignee": (issue.get("assignee") or {}).get("login", ""),
                "created_at": issue.get("created_at", ""),
            }
        )
    return _format_json(results)


@gitea_tool(
    "list_merge_requests",
    "列出指定项目的 Pull Request。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "state": {
            "type": "string",
            "enum": ["opened", "closed", "merged", "all"],
            "description": "PR 状态筛选，默认 opened",
        },
        "per_page": {
            "type": "integer",
            "description": "每页返回数量，默认 20",
        },
    },
)
def list_merge_requests(project_id, state="opened", per_page=20):
    owner, repo = _project_ref(project_id)
    query_state = "all" if state == "merged" else _normalize_issue_state_for_gitea(state)
    data = _api_json(
        "GET",
        f"repos/{owner}/{repo}/pulls",
        params={"state": query_state, "limit": per_page},
    )
    results = []
    for pr in data:
        merged = bool(pr.get("merged") or pr.get("merged_at"))
        normalized_state = "merged" if merged else _normalize_issue_state_from_gitea(pr.get("state", ""))
        if state == "merged" and not merged:
            continue
        results.append(
            {
                "iid": pr["number"],
                "title": pr["title"],
                "state": normalized_state,
                "source_branch": pr.get("head", {}).get("ref", ""),
                "target_branch": pr.get("base", {}).get("ref", ""),
                "author": (pr.get("user") or {}).get("login", ""),
            }
        )
    return _format_json(results)


@gitea_tool(
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
    owner, repo = _project_ref(project_id)
    data = _read_contents(owner, repo, file_path, ref=ref)
    if data.get("encoding") == "base64":
        raw = base64.b64decode((data.get("content") or "").encode("ascii"))
        return raw.decode("utf-8", errors="replace")[:5000]
    return str(data.get("content", ""))[:5000]


@gitea_tool(
    "read_pipeline_log",
    "读取 Actions job 的执行日志。",
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
    owner, repo = _project_ref(project_id)
    resp = _api("GET", f"repos/{owner}/{repo}/actions/jobs/{job_id}/logs")
    if resp.status_code >= 400:
        raise ToolExecutionError(f"[Gitea API 错误] {resp.status_code}: {resp.text[:500]}")
    return resp.text[:5000]


@gitea_tool(
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
    owner, repo = _project_ref(project_id)
    data = _api_json("GET", f"repos/{owner}/{repo}/branch_protections/{_encode_path(branch_name)}")
    return _format_json(data)


@gitea_tool(
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

    owner, repo = _project_ref(project_id)
    repo_meta = _api_json("GET", f"repos/{owner}/{repo}")
    default_branch = repo_meta.get("default_branch", "")
    protections = _branch_protection_map(owner, repo)
    branches_data = _api_json("GET", f"repos/{owner}/{repo}/branches", params={"limit": 100})
    prs_data = _api_json("GET", f"repos/{owner}/{repo}/pulls", params={"state": "open", "limit": 100})

    pr_branches = set()
    for pr in prs_data:
        pr_branches.add((pr.get("head") or {}).get("ref", ""))

    matched = []
    for branch in branches_data:
        name = branch["name"]
        if not fnmatch.fnmatch(name, branch_pattern):
            continue
        matched.append(
            {
                "name": name,
                "protected": name in protections or bool(branch.get("effective_branch_protection_name")),
                "default": name == default_branch,
                "merged": False,
                "has_active_mr": name in pr_branches,
            }
        )

    protected_count = sum(1 for branch in matched if branch["protected"])
    mr_count = sum(1 for branch in matched if branch["has_active_mr"])
    default_count = sum(1 for branch in matched if branch["default"])

    return _format_json(
        {
            "pattern": branch_pattern,
            "total_matched": len(matched),
            "protected_count": protected_count,
            "default_branch_count": default_count,
            "active_mr_count": mr_count,
            "branches": matched,
            "warnings": [
                warning
                for warning in [
                    f"包含 {protected_count} 个受保护分支" if protected_count else None,
                    f"包含 {default_count} 个默认分支" if default_count else None,
                    f"包含 {mr_count} 个有活跃 PR 的分支" if mr_count else None,
                ]
                if warning
            ],
        }
    )


@gitea_tool(
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
    owner, repo = _project_ref(project_id)
    params = {"state": "open", "type": "issues", "limit": 100}
    if label_filter:
        params["labels"] = label_filter
    data = _api_json("GET", f"repos/{owner}/{repo}/issues", params=params)

    matched = []
    for issue in data:
        matched.append(
            {
                "iid": issue["number"],
                "title": issue["title"],
                "labels": _normalize_labels(issue.get("labels", [])),
                "assignee": (issue.get("assignee") or {}).get("login", ""),
                "created_at": issue.get("created_at", ""),
            }
        )

    return _format_json(
        {
            "total_matched": len(matched),
            "label_filter": label_filter or "(无筛选)",
            "issues": matched,
        }
    )


@gitea_tool(
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
    owner, repo = _project_ref(project_id)
    data = _api_json("GET", f"repos/{owner}/{repo}/issues/{issue_iid}")
    issue_view = {
        "iid": data["number"],
        "title": data["title"],
        "state": _normalize_issue_state_from_gitea(data.get("state", "")),
        "labels": _normalize_labels(data.get("labels", [])),
        "assignee": (data.get("assignee") or {}).get("login", ""),
        "created_at": data.get("created_at", ""),
    }
    return _format_json(
        {
            "total_matched": 1,
            "issues": [issue_view],
            "warnings": ["该预览针对单个 issue，不会批量关闭其他 issue。"],
        }
    )


@gitea_tool(
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
    owner, repo = _project_ref(project_id)
    resp = _api("GET", f"repos/{owner}/{repo}/branch_protections/{_encode_path(branch_name)}")
    currently_protected = resp.status_code == 200
    if resp.status_code not in (200, 404):
        raise ToolExecutionError(f"[Gitea API 错误] {resp.status_code}: {resp.text[:500]}")

    warnings = []
    if action == "protect" and currently_protected:
        warnings.append("目标分支已经受保护")
    if action == "unprotect" and not currently_protected:
        warnings.append("目标分支当前未受保护")

    return _format_json(
        {
            "total_matched": 1,
            "branches": [
                {
                    "name": branch_name,
                    "currently_protected": currently_protected,
                    "requested_action": action,
                }
            ],
            "warnings": warnings,
        }
    )


@gitea_tool(
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
    owner, repo = _project_ref(project_id)
    resp = _api("DELETE", f"repos/{owner}/{repo}/branches/{_encode_path(branch_name)}")
    if resp.status_code == 204:
        return f"分支 '{branch_name}' 已删除。"
    if resp.status_code >= 400:
        raise ToolExecutionError(f"[Gitea API 错误] {resp.status_code}: {resp.text[:500]}")
    return f"分支 '{branch_name}' 删除请求已发送，状态码: {resp.status_code}"


@gitea_tool(
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
    owner, repo = _project_ref(project_id)
    resp = _api("PATCH", f"repos/{owner}/{repo}/issues/{issue_iid}", json={"state": "closed"})
    if resp.status_code >= 400:
        raise ToolExecutionError(f"[Gitea API 错误] {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    return f"Issue #{issue_iid} ('{data.get('title', '')}') 已关闭。"


@gitea_tool(
    "update_branch_protection",
    "修改分支保护规则：添加或移除保护。危险操作，可能影响合并策略和协作流程。",
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
    owner, repo = _project_ref(project_id)
    encoded_branch = _encode_path(branch_name)

    if action == "protect":
        payload = {
            "branch_name": branch_name,
            "enable_push": False,
            "enable_push_whitelist": False,
            "enable_force_push": False,
            "enable_merge_whitelist": False,
        }
        resp = _api("POST", f"repos/{owner}/{repo}/branch_protections", json=payload)
        if resp.status_code >= 400:
            raise ToolExecutionError(f"[Gitea API 错误] {resp.status_code}: {resp.text[:500]}")
        return f"分支 '{branch_name}' 已添加保护。"
    if action == "unprotect":
        resp = _api("DELETE", f"repos/{owner}/{repo}/branch_protections/{encoded_branch}")
        if resp.status_code == 204:
            return f"分支 '{branch_name}' 保护已移除。"
        if resp.status_code >= 400:
            raise ToolExecutionError(f"[Gitea API 错误] {resp.status_code}: {resp.text[:500]}")
        return f"分支 '{branch_name}' 保护移除请求已发送，状态码: {resp.status_code}"

    raise ToolExecutionError(f"[错误] action 必须是 'protect' 或 'unprotect'，收到: {action}")
