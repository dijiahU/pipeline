"""
Gitea API 工具注册 — 服务化工具架构标准

本模块直接通过 HTTP 调用 Gitea API v1。
公共接口（给 environment.py 调用）:
  get_all_schemas() -> list
  call_tool(name, args) -> str
  get_tool_names() -> list
  get_write_tool_names() -> list
"""

import base64
import json
import os
import urllib.parse

from .exceptions import ToolExecutionError
from .service_tools import ServiceToolRegistry

try:
    import requests
except ModuleNotFoundError:
    requests = None


_config = {
    "base_url": os.environ.get("GITEA_BASE_URL", "http://localhost:3000"),
    "token": os.environ.get("GITEA_ACCESS_TOKEN", "root-token"),
    "owner": os.environ.get("GITEA_OWNER", "root"),
}


_REGISTRY = ServiceToolRegistry(service_id="gitea")


def gitea_tool(name, description, params, required=None, is_write=False, group="", short_description=""):
    """装饰器：注册 Gitea 工具到 _REGISTRY"""
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


def _extract_action_jobs(payload):
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("workflow_runs", "runs", "jobs", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _list_action_jobs(owner, repo, per_page=20):
    candidates = [
        ("repos/{owner}/{repo}/actions/jobs", {"limit": per_page}),
        ("repos/{owner}/{repo}/actions/runs", {"limit": per_page}),
        ("repos/{owner}/{repo}/actions/runs", {"per_page": per_page}),
    ]
    for path_template, params in candidates:
        path = path_template.format(owner=owner, repo=repo)
        resp = _api("GET", path, params=params)
        if resp.status_code == 404:
            continue
        if resp.status_code >= 400:
            raise ToolExecutionError(f"[Gitea API 错误] {resp.status_code}: {resp.text[:500]}")
        payload = resp.json() if resp.text else []
        jobs = _extract_action_jobs(payload)
        if jobs:
            return jobs
    return []


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


def _list_directory_contents(owner, repo, directory_path="", ref="main"):
    base_path = f"repos/{owner}/{repo}/contents"
    clean_path = str(directory_path or "").strip("/")
    if clean_path:
        encoded_path = "/".join(_encode_path(part) for part in clean_path.split("/"))
        base_path = f"{base_path}/{encoded_path}"
    data = _api_json("GET", base_path, params={"ref": ref})
    if isinstance(data, dict):
        raise ToolExecutionError(f"[错误] {directory_path or '/'} 不是目录")
    return data if isinstance(data, list) else []


def _format_issue(issue):
    return {
        "iid": issue.get("number"),
        "title": issue.get("title", ""),
        "state": _normalize_issue_state_from_gitea(issue.get("state", "")),
        "labels": _normalize_labels(issue.get("labels", [])),
        "assignee": (issue.get("assignee") or {}).get("login", ""),
        "author": (issue.get("user") or {}).get("login", ""),
        "comments": issue.get("comments", 0),
        "created_at": issue.get("created_at", ""),
        "updated_at": issue.get("updated_at", ""),
        "body": issue.get("body", ""),
    }


def _format_pull_request(pr):
    merged = bool(pr.get("merged") or pr.get("merged_at"))
    return {
        "iid": pr.get("number"),
        "title": pr.get("title", ""),
        "state": "merged" if merged else _normalize_issue_state_from_gitea(pr.get("state", "")),
        "source_branch": pr.get("head", {}).get("ref", ""),
        "target_branch": pr.get("base", {}).get("ref", ""),
        "author": (pr.get("user") or {}).get("login", ""),
        "merged": merged,
        "draft": bool(pr.get("draft")),
        "created_at": pr.get("created_at", ""),
        "updated_at": pr.get("updated_at", ""),
        "body": pr.get("body", ""),
    }


def _create_branch_via_api(owner, repo, branch_name, from_ref):
    attempts = [
        {"new_branch_name": branch_name, "old_ref_name": from_ref},
        {"new_branch_name": branch_name, "old_branch_name": from_ref},
    ]
    last_error = ""
    for payload in attempts:
        resp = _api("POST", f"repos/{owner}/{repo}/branches", json=payload)
        if resp.status_code < 400:
            return resp.json() if resp.text else {"name": branch_name}
        last_error = f"[Gitea API 错误] {resp.status_code}: {resp.text[:500]}"
        if resp.status_code not in (400, 404, 409, 422):
            raise ToolExecutionError(last_error)
    raise ToolExecutionError(last_error or "[Gitea API 错误] 创建分支失败")


def _list_repo_tags_raw(owner, repo, per_page=20):
    data = _api_json("GET", f"repos/{owner}/{repo}/tags", params={"limit": per_page})
    return data if isinstance(data, list) else []


@gitea_tool(
    "list_projects",
    "列出 Gitea 上所有可见项目。",
    {
        "per_page": {
            "type": "integer",
            "description": "每页返回数量，默认 20，最大 100",
        },
    },
    group="repo_info",
    short_description="列出当前可见的仓库项目",
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
    group="repo_info",
    short_description="读取单个仓库的详细元数据",
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
    group="branch_ops",
    short_description="列出仓库分支及默认分支信息",
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
    group="issue_tracking",
    short_description="按状态列出仓库 issue",
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
    group="pull_requests",
    short_description="按状态列出仓库 Pull Request",
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
    group="repo_content",
    short_description="读取仓库文件内容",
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
    group="ci_cd",
    short_description="读取指定 CI job 的日志",
)
def read_pipeline_log(project_id, job_id):
    owner, repo = _project_ref(project_id)
    resp = _api("GET", f"repos/{owner}/{repo}/actions/jobs/{job_id}/logs")
    if resp.status_code >= 400:
        raise ToolExecutionError(f"[Gitea API 错误] {resp.status_code}: {resp.text[:500]}")
    return resp.text[:5000]


@gitea_tool(
    "list_pipeline_jobs",
    "列出仓库最近的 Actions / CI job。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "per_page": {
            "type": "integer",
            "description": "返回数量，默认 10",
        },
    },
    group="ci_cd",
    short_description="列出最近的 CI 或 Actions 任务",
)
def list_pipeline_jobs(project_id, per_page=10):
    owner, repo = _project_ref(project_id)
    jobs = _list_action_jobs(owner, repo, per_page=per_page)
    results = []
    for job in jobs:
        results.append(
            {
                "id": job.get("id") or job.get("run_number") or job.get("number"),
                "name": job.get("name", "") or job.get("display_title", ""),
                "status": job.get("status", "") or job.get("conclusion", ""),
                "head_branch": job.get("head_branch", "") or job.get("ref", ""),
                "created_at": job.get("created_at", "") or job.get("run_started_at", ""),
            }
        )
    return _format_json(results)


@gitea_tool(
    "get_latest_pipeline_log",
    "读取仓库最近一次 Actions / CI job 的执行日志。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
    },
    group="ci_cd",
    short_description="读取最近一次 CI job 的日志摘要",
)
def get_latest_pipeline_log(project_id):
    owner, repo = _project_ref(project_id)
    jobs = _list_action_jobs(owner, repo, per_page=1)
    if not jobs:
        return _format_json(
            {
                "project_id": project_id,
                "job_found": False,
                "message": "该仓库当前没有可读取的 Actions / CI job 日志。",
            }
        )

    latest = jobs[0]
    job_id = latest.get("id")
    if not job_id:
        return _format_json(
            {
                "project_id": project_id,
                "job_found": False,
                "message": "找到了最近一次 Actions / CI 记录，但缺少可读取日志的 job_id。",
            }
        )

    log_text = read_pipeline_log(project_id, str(job_id))
    return _format_json(
        {
            "project_id": project_id,
            "job_found": True,
            "job_id": str(job_id),
            "job_name": latest.get("name", "") or latest.get("display_title", ""),
            "status": latest.get("status", "") or latest.get("conclusion", ""),
            "log_excerpt": log_text,
        }
    )


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
    group="branch_ops",
    short_description="读取分支保护规则",
)
def get_branch_protection(project_id, branch_name):
    owner, repo = _project_ref(project_id)
    data = _api_json("GET", f"repos/{owner}/{repo}/branch_protections/{_encode_path(branch_name)}")
    return _format_json(data)


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
    is_write=True,
    group="branch_ops",
    short_description="删除单个非默认分支",
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
    is_write=True,
    group="issue_tracking",
    short_description="关闭单个 issue",
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
    is_write=True,
    group="branch_ops",
    short_description="添加或移除分支保护规则",
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


@gitea_tool(
    "get_repo_settings",
    "读取仓库的主要设置和能力开关。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID、仓库名，或路径（如 'root/openclaw'）",
        },
    },
    group="repo_info",
    short_description="读取仓库设置和功能开关",
)
def get_repo_settings(project_id):
    data = _repo_meta(project_id)
    result = {
        "id": data.get("id"),
        "name": data.get("name", ""),
        "full_name": data.get("full_name", ""),
        "default_branch": data.get("default_branch", ""),
        "private": bool(data.get("private")),
        "archived": bool(data.get("archived")),
        "mirror": bool(data.get("mirror")),
        "has_issues": bool(data.get("has_issues")),
        "has_pull_requests": bool(data.get("has_pull_requests")),
        "has_projects": bool(data.get("has_projects")),
        "has_wiki": bool(data.get("has_wiki")),
        "website": data.get("website", ""),
        "description": data.get("description", ""),
    }
    return _format_json(result)


@gitea_tool(
    "list_repo_tags",
    "列出仓库当前可见的 tag。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "per_page": {
            "type": "integer",
            "description": "返回数量，默认 20",
        },
    },
    group="repo_info",
    short_description="列出仓库 tag 和对应提交",
)
def list_repo_tags(project_id, per_page=20):
    owner, repo = _project_ref(project_id)
    tags = _list_repo_tags_raw(owner, repo, per_page=per_page)
    results = []
    for tag in tags:
        commit = tag.get("commit") or {}
        results.append(
            {
                "name": tag.get("name", ""),
                "id": tag.get("id", ""),
                "commit_sha": commit.get("sha", ""),
                "zipball_url": tag.get("zipball_url", ""),
                "tarball_url": tag.get("tarball_url", ""),
            }
        )
    return _format_json(results)


@gitea_tool(
    "list_repo_directory",
    "列出仓库目录中的文件和子目录。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "directory_path": {
            "type": "string",
            "description": "目录路径；留空表示仓库根目录",
        },
        "ref": {
            "type": "string",
            "description": "分支名或 commit SHA，默认 main",
        },
    },
    group="repo_content",
    short_description="列出仓库目录结构",
)
def list_repo_directory(project_id, directory_path="", ref="main"):
    owner, repo = _project_ref(project_id)
    entries = _list_directory_contents(owner, repo, directory_path=directory_path, ref=ref)
    results = []
    for item in entries:
        results.append(
            {
                "name": item.get("name", ""),
                "path": item.get("path", ""),
                "type": item.get("type", ""),
                "size": item.get("size", 0),
                "sha": item.get("sha", ""),
            }
        )
    return _format_json(results)


@gitea_tool(
    "create_branch",
    "从现有分支或 ref 创建一个新分支。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "branch_name": {
            "type": "string",
            "description": "新分支名称",
        },
        "from_ref": {
            "type": "string",
            "description": "源分支或 ref，默认 main",
        },
    },
    is_write=True,
    group="branch_ops",
    short_description="从现有 ref 创建新分支",
)
def create_branch(project_id, branch_name, from_ref="main"):
    owner, repo = _project_ref(project_id)
    data = _create_branch_via_api(owner, repo, branch_name, from_ref)
    return _format_json(
        {
            "name": data.get("name", branch_name),
            "created_from": from_ref,
            "commit_sha": (data.get("commit") or {}).get("id", ""),
        }
    )


@gitea_tool(
    "get_issue",
    "读取单个 issue 的详细信息。",
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
    group="issue_tracking",
    short_description="读取单个 issue 详情",
)
def get_issue(project_id, issue_iid):
    owner, repo = _project_ref(project_id)
    data = _api_json("GET", f"repos/{owner}/{repo}/issues/{issue_iid}")
    return _format_json(_format_issue(data))


@gitea_tool(
    "list_issue_comments",
    "列出指定 issue 下的评论。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "issue_iid": {
            "type": "integer",
            "description": "Issue 的项目内编号（iid）",
        },
        "per_page": {
            "type": "integer",
            "description": "返回数量，默认 20",
        },
    },
    group="issue_tracking",
    short_description="列出 issue 评论记录",
)
def list_issue_comments(project_id, issue_iid, per_page=20):
    owner, repo = _project_ref(project_id)
    data = _api_json(
        "GET",
        f"repos/{owner}/{repo}/issues/{issue_iid}/comments",
        params={"limit": per_page},
    )
    results = []
    for item in data if isinstance(data, list) else []:
        results.append(
            {
                "id": item.get("id"),
                "author": (item.get("user") or {}).get("login", ""),
                "created_at": item.get("created_at", ""),
                "updated_at": item.get("updated_at", ""),
                "body": item.get("body", ""),
            }
        )
    return _format_json(results)


@gitea_tool(
    "create_issue",
    "在仓库中创建一个新的 issue。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "title": {
            "type": "string",
            "description": "Issue 标题",
        },
        "body": {
            "type": "string",
            "description": "Issue 正文，可为空",
        },
    },
    is_write=True,
    group="issue_tracking",
    short_description="创建新的 issue",
)
def create_issue(project_id, title, body=""):
    owner, repo = _project_ref(project_id)
    payload = {"title": title}
    if body:
        payload["body"] = body
    data = _api_json("POST", f"repos/{owner}/{repo}/issues", json=payload)
    return _format_json(_format_issue(data))


@gitea_tool(
    "add_issue_comment",
    "给指定 issue 添加评论。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "issue_iid": {
            "type": "integer",
            "description": "Issue 的项目内编号（iid）",
        },
        "body": {
            "type": "string",
            "description": "评论内容",
        },
    },
    is_write=True,
    group="issue_tracking",
    short_description="向 issue 添加评论",
)
def add_issue_comment(project_id, issue_iid, body):
    owner, repo = _project_ref(project_id)
    data = _api_json(
        "POST",
        f"repos/{owner}/{repo}/issues/{issue_iid}/comments",
        json={"body": body},
    )
    return _format_json(
        {
            "id": data.get("id"),
            "issue_iid": issue_iid,
            "author": (data.get("user") or {}).get("login", ""),
            "created_at": data.get("created_at", ""),
            "body": data.get("body", ""),
        }
    )


@gitea_tool(
    "reopen_issue",
    "重新打开一个已关闭的 issue。",
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
    is_write=True,
    group="issue_tracking",
    short_description="重新打开单个 issue",
)
def reopen_issue(project_id, issue_iid):
    owner, repo = _project_ref(project_id)
    data = _api_json("PATCH", f"repos/{owner}/{repo}/issues/{issue_iid}", json={"state": "open"})
    return f"Issue #{issue_iid} ('{data.get('title', '')}') 已重新打开。"


@gitea_tool(
    "get_pull_request",
    "读取单个 Pull Request 的详细信息。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "pr_iid": {
            "type": "integer",
            "description": "Pull Request 的项目内编号（iid）",
        },
    },
    group="pull_requests",
    short_description="读取单个 Pull Request 详情",
)
def get_pull_request(project_id, pr_iid):
    owner, repo = _project_ref(project_id)
    data = _api_json("GET", f"repos/{owner}/{repo}/pulls/{pr_iid}")
    return _format_json(_format_pull_request(data))


@gitea_tool(
    "list_pull_request_files",
    "列出 Pull Request 变更涉及的文件。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "pr_iid": {
            "type": "integer",
            "description": "Pull Request 的项目内编号（iid）",
        },
    },
    group="pull_requests",
    short_description="列出 Pull Request 的变更文件",
)
def list_pull_request_files(project_id, pr_iid):
    owner, repo = _project_ref(project_id)
    data = _api_json("GET", f"repos/{owner}/{repo}/pulls/{pr_iid}/files")
    results = []
    for item in data if isinstance(data, list) else []:
        results.append(
            {
                "filename": item.get("filename", "") or item.get("path", ""),
                "status": item.get("status", ""),
                "additions": item.get("additions", 0),
                "deletions": item.get("deletions", 0),
                "changes": item.get("changes", 0),
                "patch": item.get("patch", "")[:1000],
            }
        )
    return _format_json(results)


@gitea_tool(
    "create_pull_request",
    "从源分支向目标分支创建 Pull Request。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "title": {
            "type": "string",
            "description": "Pull Request 标题",
        },
        "head_branch": {
            "type": "string",
            "description": "源分支名",
        },
        "base_branch": {
            "type": "string",
            "description": "目标分支名",
        },
        "body": {
            "type": "string",
            "description": "Pull Request 描述，可为空",
        },
    },
    is_write=True,
    group="pull_requests",
    short_description="创建新的 Pull Request",
)
def create_pull_request(project_id, title, head_branch, base_branch, body=""):
    owner, repo = _project_ref(project_id)
    payload = {
        "title": title,
        "head": head_branch,
        "base": base_branch,
    }
    if body:
        payload["body"] = body
    data = _api_json("POST", f"repos/{owner}/{repo}/pulls", json=payload)
    return _format_json(_format_pull_request(data))


@gitea_tool(
    "list_collaborators",
    "列出仓库协作者及其权限。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "per_page": {
            "type": "integer",
            "description": "返回数量，默认 20",
        },
    },
    group="access_control",
    short_description="列出仓库协作者和权限",
)
def list_collaborators(project_id, per_page=20):
    owner, repo = _project_ref(project_id)
    data = _api_json(
        "GET",
        f"repos/{owner}/{repo}/collaborators",
        params={"limit": per_page},
    )
    results = []
    for item in data if isinstance(data, list) else []:
        results.append(
            {
                "login": item.get("login", ""),
                "full_name": item.get("full_name", ""),
                "email": item.get("email", ""),
                "permissions": item.get("permissions", {}),
            }
        )
    return _format_json(results)


@gitea_tool(
    "add_collaborator",
    "把一个用户添加为仓库协作者。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "username": {
            "type": "string",
            "description": "要添加的用户名",
        },
        "permission": {
            "type": "string",
            "enum": ["read", "write", "admin"],
            "description": "授予的权限级别，默认 write",
        },
    },
    is_write=True,
    group="access_control",
    short_description="为仓库添加协作者",
)
def add_collaborator(project_id, username, permission="write"):
    owner, repo = _project_ref(project_id)
    resp = _api(
        "PUT",
        f"repos/{owner}/{repo}/collaborators/{_encode_path(username)}",
        json={"permission": permission},
    )
    if resp.status_code not in (201, 204):
        raise ToolExecutionError(f"[Gitea API 错误] {resp.status_code}: {resp.text[:500]}")
    return f"用户 '{username}' 已添加为 {project_id} 的协作者，权限={permission}。"


@gitea_tool(
    "remove_collaborator",
    "移除仓库协作者。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "username": {
            "type": "string",
            "description": "要移除的用户名",
        },
    },
    is_write=True,
    group="access_control",
    short_description="移除仓库协作者",
)
def remove_collaborator(project_id, username):
    owner, repo = _project_ref(project_id)
    resp = _api("DELETE", f"repos/{owner}/{repo}/collaborators/{_encode_path(username)}")
    if resp.status_code not in (204, 404):
        raise ToolExecutionError(f"[Gitea API 错误] {resp.status_code}: {resp.text[:500]}")
    if resp.status_code == 404:
        return f"用户 '{username}' 不是 {project_id} 的协作者。"
    return f"用户 '{username}' 已从 {project_id} 协作者列表中移除。"


@gitea_tool(
    "list_deploy_keys",
    "列出仓库的 deploy key。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
    },
    group="access_control",
    short_description="列出仓库 deploy key",
)
def list_deploy_keys(project_id):
    owner, repo = _project_ref(project_id)
    data = _api_json("GET", f"repos/{owner}/{repo}/keys")
    results = []
    for item in data if isinstance(data, list) else []:
        key_value = item.get("key", "")
        results.append(
            {
                "id": item.get("id"),
                "title": item.get("title", ""),
                "read_only": bool(item.get("read_only")),
                "fingerprint": item.get("fingerprint", ""),
                "key_preview": f"{key_value[:60]}..." if key_value else "",
            }
        )
    return _format_json(results)


@gitea_tool(
    "add_deploy_key",
    "给仓库添加一个 deploy key。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "title": {
            "type": "string",
            "description": "Deploy key 标题",
        },
        "public_key": {
            "type": "string",
            "description": "SSH 公钥内容",
        },
        "read_only": {
            "type": "boolean",
            "description": "是否只读，默认 true",
        },
    },
    is_write=True,
    group="access_control",
    short_description="向仓库添加 deploy key",
)
def add_deploy_key(project_id, title, public_key, read_only=True):
    owner, repo = _project_ref(project_id)
    data = _api_json(
        "POST",
        f"repos/{owner}/{repo}/keys",
        json={"title": title, "key": public_key, "read_only": read_only},
    )
    return _format_json(
        {
            "id": data.get("id"),
            "title": data.get("title", title),
            "read_only": bool(data.get("read_only", read_only)),
        }
    )


@gitea_tool(
    "remove_deploy_key",
    "删除仓库中的一个 deploy key。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "key_id": {
            "type": "integer",
            "description": "Deploy key 的 ID",
        },
    },
    is_write=True,
    group="access_control",
    short_description="删除单个 deploy key",
)
def remove_deploy_key(project_id, key_id):
    owner, repo = _project_ref(project_id)
    resp = _api("DELETE", f"repos/{owner}/{repo}/keys/{key_id}")
    if resp.status_code not in (204, 404):
        raise ToolExecutionError(f"[Gitea API 错误] {resp.status_code}: {resp.text[:500]}")
    if resp.status_code == 404:
        return f"Deploy key #{key_id} 不存在。"
    return f"Deploy key #{key_id} 已删除。"


@gitea_tool(
    "list_repo_labels",
    "列出仓库中的 label。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "per_page": {
            "type": "integer",
            "description": "返回数量，默认 20",
        },
    },
    group="labels_and_milestones",
    short_description="列出仓库 label",
)
def list_repo_labels(project_id, per_page=20):
    owner, repo = _project_ref(project_id)
    data = _api_json(
        "GET",
        f"repos/{owner}/{repo}/labels",
        params={"limit": per_page},
    )
    results = []
    for item in data if isinstance(data, list) else []:
        results.append(
            {
                "id": item.get("id"),
                "name": item.get("name", ""),
                "color": item.get("color", ""),
                "description": item.get("description", ""),
            }
        )
    return _format_json(results)


@gitea_tool(
    "create_label",
    "在仓库中创建一个新的 label。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "name": {
            "type": "string",
            "description": "Label 名称",
        },
        "color": {
            "type": "string",
            "description": "Label 颜色，形如 ff0000 或 #ff0000",
        },
        "description": {
            "type": "string",
            "description": "Label 描述，可为空",
        },
    },
    is_write=True,
    group="labels_and_milestones",
    short_description="创建新的仓库 label",
)
def create_label(project_id, name, color, description=""):
    owner, repo = _project_ref(project_id)
    payload = {
        "name": name,
        "color": str(color).lstrip("#"),
    }
    if description:
        payload["description"] = description
    data = _api_json("POST", f"repos/{owner}/{repo}/labels", json=payload)
    return _format_json(
        {
            "id": data.get("id"),
            "name": data.get("name", name),
            "color": data.get("color", payload["color"]),
            "description": data.get("description", description),
        }
    )


@gitea_tool(
    "list_milestones",
    "列出仓库中的 milestone。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "state": {
            "type": "string",
            "enum": ["open", "closed", "all"],
            "description": "Milestone 状态筛选，默认 open",
        },
        "per_page": {
            "type": "integer",
            "description": "返回数量，默认 20",
        },
    },
    group="labels_and_milestones",
    short_description="列出仓库 milestone",
)
def list_milestones(project_id, state="open", per_page=20):
    owner, repo = _project_ref(project_id)
    data = _api_json(
        "GET",
        f"repos/{owner}/{repo}/milestones",
        params={"state": state, "limit": per_page},
    )
    results = []
    for item in data if isinstance(data, list) else []:
        results.append(
            {
                "id": item.get("id"),
                "title": item.get("title", ""),
                "state": item.get("state", ""),
                "open_issues": item.get("open_issues", 0),
                "closed_issues": item.get("closed_issues", 0),
                "due_on": item.get("due_on", ""),
                "description": item.get("description", ""),
            }
        )
    return _format_json(results)


@gitea_tool(
    "create_milestone",
    "在仓库中创建一个新的 milestone。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "title": {
            "type": "string",
            "description": "Milestone 标题",
        },
        "description": {
            "type": "string",
            "description": "Milestone 描述，可为空",
        },
        "due_on": {
            "type": "string",
            "description": "截止时间，ISO8601 格式，可为空",
        },
    },
    is_write=True,
    group="labels_and_milestones",
    short_description="创建新的 milestone",
)
def create_milestone(project_id, title, description="", due_on=""):
    owner, repo = _project_ref(project_id)
    payload = {"title": title}
    if description:
        payload["description"] = description
    if due_on:
        payload["due_on"] = due_on
    data = _api_json("POST", f"repos/{owner}/{repo}/milestones", json=payload)
    return _format_json(
        {
            "id": data.get("id"),
            "title": data.get("title", title),
            "state": data.get("state", ""),
            "due_on": data.get("due_on", due_on),
        }
    )


@gitea_tool(
    "list_releases",
    "列出仓库中的 release。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "per_page": {
            "type": "integer",
            "description": "返回数量，默认 20",
        },
    },
    group="releases",
    short_description="列出仓库 release",
)
def list_releases(project_id, per_page=20):
    owner, repo = _project_ref(project_id)
    data = _api_json(
        "GET",
        f"repos/{owner}/{repo}/releases",
        params={"limit": per_page},
    )
    results = []
    for item in data if isinstance(data, list) else []:
        results.append(
            {
                "id": item.get("id"),
                "tag_name": item.get("tag_name", ""),
                "name": item.get("name", ""),
                "target_commitish": item.get("target_commitish", ""),
                "draft": bool(item.get("draft")),
                "prerelease": bool(item.get("prerelease")),
                "created_at": item.get("created_at", ""),
                "published_at": item.get("published_at", ""),
            }
        )
    return _format_json(results)


@gitea_tool(
    "create_release",
    "创建一个新的 release。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "tag_name": {
            "type": "string",
            "description": "Release 对应的 tag 名称",
        },
        "target_commitish": {
            "type": "string",
            "description": "目标分支或 commit，默认 main",
        },
        "name": {
            "type": "string",
            "description": "Release 名称；留空则使用 tag_name",
        },
        "body": {
            "type": "string",
            "description": "Release 描述，可为空",
        },
        "draft": {
            "type": "boolean",
            "description": "是否为草稿，默认 false",
        },
        "prerelease": {
            "type": "boolean",
            "description": "是否为预发布，默认 false",
        },
    },
    is_write=True,
    group="releases",
    short_description="创建新的 release",
)
def create_release(project_id, tag_name, target_commitish="main", name="", body="", draft=False, prerelease=False):
    owner, repo = _project_ref(project_id)
    payload = {
        "tag_name": tag_name,
        "target_commitish": target_commitish,
        "name": name or tag_name,
        "draft": draft,
        "prerelease": prerelease,
    }
    if body:
        payload["body"] = body
    data = _api_json("POST", f"repos/{owner}/{repo}/releases", json=payload)
    return _format_json(
        {
            "id": data.get("id"),
            "tag_name": data.get("tag_name", tag_name),
            "name": data.get("name", name or tag_name),
            "draft": bool(data.get("draft", draft)),
            "prerelease": bool(data.get("prerelease", prerelease)),
        }
    )


@gitea_tool(
    "delete_release",
    "删除一个 release。危险操作，不可逆。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "release_id": {
            "type": "integer",
            "description": "Release 的 ID",
        },
    },
    is_write=True,
    group="releases",
    short_description="删除单个 release",
)
def delete_release(project_id, release_id):
    owner, repo = _project_ref(project_id)
    resp = _api("DELETE", f"repos/{owner}/{repo}/releases/{release_id}")
    if resp.status_code not in (204, 404):
        raise ToolExecutionError(f"[Gitea API 错误] {resp.status_code}: {resp.text[:500]}")
    if resp.status_code == 404:
        return f"Release #{release_id} 不存在。"
    return f"Release #{release_id} 已删除。"


@gitea_tool(
    "list_webhooks",
    "列出仓库的 webhook。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
    },
    group="webhooks",
    short_description="列出仓库 webhook",
)
def list_webhooks(project_id):
    owner, repo = _project_ref(project_id)
    data = _api_json("GET", f"repos/{owner}/{repo}/hooks")
    hooks = data.get("data", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    results = []
    for item in hooks:
        config = item.get("config") or {}
        results.append(
            {
                "id": item.get("id"),
                "type": item.get("type", ""),
                "active": bool(item.get("active")),
                "events": item.get("events", []),
                "url": config.get("url", ""),
                "content_type": config.get("content_type", ""),
            }
        )
    return _format_json(results)


@gitea_tool(
    "create_webhook",
    "给仓库创建一个新的 webhook。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "url": {
            "type": "string",
            "description": "Webhook 目标地址",
        },
        "events": {
            "type": "array",
            "items": {"type": "string"},
            "description": "订阅的事件列表，默认 ['push']",
        },
        "hook_type": {
            "type": "string",
            "description": "Webhook 类型，默认 gitea",
        },
        "content_type": {
            "type": "string",
            "description": "发送内容格式，默认 json",
        },
        "secret": {
            "type": "string",
            "description": "Webhook 签名密钥，可为空",
        },
        "active": {
            "type": "boolean",
            "description": "是否启用，默认 true",
        },
    },
    is_write=True,
    group="webhooks",
    short_description="创建新的 webhook",
)
def create_webhook(project_id, url, events=None, hook_type="gitea", content_type="json", secret="", active=True):
    owner, repo = _project_ref(project_id)
    payload = {
        "type": hook_type,
        "active": active,
        "events": events or ["push"],
        "config": {
            "url": url,
            "content_type": content_type,
        },
    }
    if secret:
        payload["config"]["secret"] = secret
    data = _api_json("POST", f"repos/{owner}/{repo}/hooks", json=payload)
    config = data.get("config") or {}
    return _format_json(
        {
            "id": data.get("id"),
            "type": data.get("type", hook_type),
            "active": bool(data.get("active", active)),
            "events": data.get("events", payload["events"]),
            "url": config.get("url", url),
        }
    )


@gitea_tool(
    "delete_webhook",
    "删除仓库中的一个 webhook。危险操作，不可逆。",
    {
        "project_id": {
            "type": "string",
            "description": "项目 ID 或路径",
        },
        "hook_id": {
            "type": "integer",
            "description": "Webhook 的 ID",
        },
    },
    is_write=True,
    group="webhooks",
    short_description="删除单个 webhook",
)
def delete_webhook(project_id, hook_id):
    owner, repo = _project_ref(project_id)
    resp = _api("DELETE", f"repos/{owner}/{repo}/hooks/{hook_id}")
    if resp.status_code not in (204, 404):
        raise ToolExecutionError(f"[Gitea API 错误] {resp.status_code}: {resp.text[:500]}")
    if resp.status_code == 404:
        return f"Webhook #{hook_id} 不存在。"
    return f"Webhook #{hook_id} 已删除。"
