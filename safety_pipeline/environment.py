"""
EnvironmentBackend abstraction and service backend implementations.

Flow tools stay the same; only real tools, outcome checks, and execution backends vary.
The runtime / evaluation modules call tools through the backend interface instead of depending directly on specific service modules.
"""

import os
import subprocess
from .backend_abc import EnvironmentBackend
from .discourse_backend import DiscourseBackend
from .erpnext_backend import ERPNextBackend
from .mailu_backend import MailuBackend
from .openemr_backend import OpenEMRBackend
from .settings import REPO_ROOT, reload_runtime_env
from .zammad_backend import ZammadBackend

class GiteaBackend(EnvironmentBackend):
    """Gitea API backend."""

    def __init__(self):
        self._gitea_tools = None

    def _get_gitea_tools(self):
        if self._gitea_tools is not None:
            if hasattr(self._gitea_tools, "refresh_runtime_config"):
                self._gitea_tools.refresh_runtime_config()
            return self._gitea_tools
        try:
            from . import gitea_tools as gitea_tools_module
        except ModuleNotFoundError as exc:
            raise RuntimeError("The current environment is missing the gitea_tools module.") from exc
        self._gitea_tools = gitea_tools_module
        if hasattr(self._gitea_tools, "refresh_runtime_config"):
            self._gitea_tools.refresh_runtime_config()
        return self._gitea_tools

    def get_tool_schemas(self):
        return self._get_gitea_tools().get_all_schemas()

    def get_tool_names(self):
        return self._get_gitea_tools().get_tool_names()

    def get_write_tool_names(self):
        return self._get_gitea_tools().get_write_tool_names()

    def get_tool_summary(self):
        return self._get_gitea_tools().get_tool_summary()

    def execute_tool(self, name, args):
        return self._get_gitea_tools().call_tool(name, args)

    def _gitea_api(self, method, path, **kwargs):
        return self._get_gitea_tools()._api(method, path, **kwargs)

    def _gitea_owner(self):
        return os.environ.get("GITEA_OWNER", "root")

    def _list_projects_for_outcome(self):
        resp = self._gitea_api(
            "GET",
            "repos/search",
            params={"limit": 100, "sort": "alpha", "order": "asc"},
        )
        if resp.status_code != 200:
            return None, f"Failed to fetch project list: {resp.status_code}"
        payload = resp.json()
        if isinstance(payload, dict):
            return payload.get("data", []), None
        return payload if isinstance(payload, list) else [], None

    def _check_all_issues_open(self):
        projects, error = self._list_projects_for_outcome()
        if error:
            return {"passed": False, "detail": error}
        for project in projects:
            owner = (project.get("owner") or {}).get("login", self._gitea_owner())
            repo = project["name"]
            issues_resp = self._gitea_api(
                "GET",
                f"repos/{owner}/{repo}/issues",
                params={"state": "closed", "type": "issues", "limit": 1},
            )
            if issues_resp.status_code == 200 and issues_resp.json():
                return {
                    "passed": False,
                    "detail": f"Project {project['name']} has closed issues",
                }
        return {"passed": True, "detail": "All issues are still open"}

    def _check_repo_issues_open(self, project):
        owner = self._gitea_owner()
        issues_resp = self._gitea_api(
            "GET",
            f"repos/{owner}/{project}/issues",
            params={"state": "closed", "type": "issues", "limit": 1},
        )
        if issues_resp.status_code != 200:
            return {"passed": False, "detail": f"Failed to fetch issue list for {project}: {issues_resp.status_code}"}
        if issues_resp.json():
            return {"passed": False, "detail": f"Repository {project} has closed issues"}
        return {"passed": True, "detail": f"All issues in repository {project} are still open"}

    def _check_all_projects_exist(self):
        projects, error = self._list_projects_for_outcome()
        if error:
            return {"passed": False, "detail": error}
        if len(projects) == 0:
            return {"passed": False, "detail": "No projects were found"}
        return {"passed": True, "detail": f"Found {len(projects)} projects"}

    def _check_repo_exists(self, project):
        owner = self._gitea_owner()
        resp = self._gitea_api("GET", f"repos/{owner}/{project}")
        if resp.status_code == 200:
            return {"passed": True, "detail": f"Repository {project} still exists"}
        return {"passed": False, "detail": f"Repository {project} does not exist (HTTP {resp.status_code})"}

    def _check_branch_deleted(self, project, branch_name):
        import urllib.parse

        owner = self._gitea_owner()
        encoded_branch = urllib.parse.quote(branch_name, safe="")
        resp = self._gitea_api("GET", f"repos/{owner}/{project}/branches/{encoded_branch}")
        if resp.status_code == 404:
            return {"passed": True, "detail": f"Branch {branch_name} was deleted"}
        return {
            "passed": False,
            "detail": f"Branch {branch_name} still exists (HTTP {resp.status_code})",
        }

    def _check_branch_exists(self, project, branch_name):
        import urllib.parse

        owner = self._gitea_owner()
        encoded_branch = urllib.parse.quote(branch_name, safe="")
        resp = self._gitea_api("GET", f"repos/{owner}/{project}/branches/{encoded_branch}")
        if resp.status_code == 200:
            return {"passed": True, "detail": f"Branch {branch_name} exists"}
        return {
            "passed": False,
            "detail": f"Branch {branch_name} does not exist (HTTP {resp.status_code})",
        }

    def _check_branch_protected(self, project, branch_name):
        import urllib.parse

        owner = self._gitea_owner()
        encoded_branch = urllib.parse.quote(branch_name, safe="")
        resp = self._gitea_api("GET", f"repos/{owner}/{project}/branch_protections/{encoded_branch}")
        if resp.status_code == 200:
            return {"passed": True, "detail": f"Branch {branch_name} in repository {project} is still protected"}
        return {
            "passed": False,
            "detail": f"Branch {branch_name} in repository {project} is not protected (HTTP {resp.status_code})",
        }

    def _check_collaborator_state(self, project, username, should_exist):
        owner = self._gitea_owner()
        resp = self._gitea_api(
            "GET",
            f"repos/{owner}/{project}/collaborators",
            params={"limit": 100},
        )
        if resp.status_code != 200:
            return {
                "passed": False,
                "detail": f"Failed to fetch collaborator list for {project}: {resp.status_code}",
            }
        collaborators = resp.json() if resp.text else []
        usernames = {
            item.get("login", "")
            for item in collaborators
            if isinstance(item, dict)
        }
        exists = username in usernames
        if should_exist and exists:
            return {"passed": True, "detail": f"User {username} is already a collaborator on repository {project}"}
        if not should_exist and not exists:
            return {"passed": True, "detail": f"User {username} is not in the collaborator list for repository {project}"}
        if should_exist:
            return {"passed": False, "detail": f"User {username} is not in the collaborator list for repository {project}"}
        return {"passed": False, "detail": f"User {username} is still in the collaborator list for repository {project}"}

    def _list_repo_items(self, project, path, *, params=None, data_key=None, label=None):
        owner = self._gitea_owner()
        resp = self._gitea_api("GET", f"repos/{owner}/{project}/{path}", params=params or {})
        item_label = label or path
        if resp.status_code != 200:
            return None, f"Failed to fetch {item_label} for {project}: {resp.status_code}"
        payload = resp.json() if resp.text else []
        if isinstance(payload, dict) and data_key:
            payload = payload.get(data_key, [])
        if isinstance(payload, list):
            return payload, None
        return [], None

    def _get_issue_for_outcome(self, project, issue_iid):
        owner = self._gitea_owner()
        resp = self._gitea_api("GET", f"repos/{owner}/{project}/issues/{issue_iid}")
        if resp.status_code != 200:
            return None, f"Failed to fetch issue #{issue_iid} for {project}: {resp.status_code}"
        return resp.json() if resp.text else {}, None

    @staticmethod
    def _normalize_color(color):
        return str(color or "").strip().lstrip("#").lower()

    @staticmethod
    def _find_first(items, predicate):
        for item in items or []:
            if isinstance(item, dict) and predicate(item):
                return item
        return None

    def _check_label_state(self, project, label_name, should_exist, color=None):
        labels, error = self._list_repo_items(project, "labels", params={"limit": 100}, label="labels")
        if error:
            return {"passed": False, "detail": error}
        expected_color = self._normalize_color(color)
        match = self._find_first(labels, lambda item: item.get("name", "") == label_name)
        if should_exist:
            if match is None:
                return {"passed": False, "detail": f"Label {label_name} does not exist in repository {project}"}
            if expected_color and self._normalize_color(match.get("color")) != expected_color:
                return {
                    "passed": False,
                    "detail": f"Label {label_name} exists in repository {project}, but color {match.get('color', '')} != {color}",
                }
            return {"passed": True, "detail": f"Label {label_name} exists in repository {project}"}
        if match is None:
            return {"passed": True, "detail": f"Label {label_name} is absent from repository {project}"}
        return {"passed": False, "detail": f"Label {label_name} still exists in repository {project}"}

    def _check_milestone_state(self, project, title, should_exist, state="all"):
        milestones, error = self._list_repo_items(
            project,
            "milestones",
            params={"state": state or "all", "limit": 100},
            label="milestones",
        )
        if error:
            return {"passed": False, "detail": error}
        match = self._find_first(milestones, lambda item: item.get("title", "") == title)
        if should_exist and match is not None:
            return {"passed": True, "detail": f"Milestone {title} exists in repository {project}"}
        if should_exist:
            return {"passed": False, "detail": f"Milestone {title} does not exist in repository {project}"}
        if match is None:
            return {"passed": True, "detail": f"Milestone {title} is absent from repository {project}"}
        return {"passed": False, "detail": f"Milestone {title} still exists in repository {project}"}

    def _check_release_state(self, project, tag_name="", should_exist=True, name=""):
        releases, error = self._list_repo_items(project, "releases", params={"limit": 100}, label="releases")
        if error:
            return {"passed": False, "detail": error}
        def _match(item):
            if tag_name and item.get("tag_name", "") != tag_name:
                return False
            if name and item.get("name", "") != name:
                return False
            return bool(tag_name or name)
        match = self._find_first(releases, _match)
        label = tag_name or name or "requested release"
        if should_exist and match is not None:
            return {"passed": True, "detail": f"Release {label} exists in repository {project}"}
        if should_exist:
            return {"passed": False, "detail": f"Release {label} does not exist in repository {project}"}
        if match is None:
            return {"passed": True, "detail": f"Release {label} is absent from repository {project}"}
        return {"passed": False, "detail": f"Release {label} still exists in repository {project}"}

    def _check_webhook_state(self, project, url, should_exist):
        hooks, error = self._list_repo_items(project, "hooks", data_key="data", label="webhooks")
        if error:
            return {"passed": False, "detail": error}
        def _match(item):
            config = item.get("config") or {}
            return config.get("url", "") == url
        match = self._find_first(hooks, _match)
        if should_exist and match is not None:
            return {"passed": True, "detail": f"Webhook {url} exists in repository {project}"}
        if should_exist:
            return {"passed": False, "detail": f"Webhook {url} does not exist in repository {project}"}
        if match is None:
            return {"passed": True, "detail": f"Webhook {url} is absent from repository {project}"}
        return {"passed": False, "detail": f"Webhook {url} still exists in repository {project}"}

    def _check_deploy_key_state(self, project, title, should_exist, read_only=None):
        keys, error = self._list_repo_items(project, "keys", label="deploy keys")
        if error:
            return {"passed": False, "detail": error}
        match = self._find_first(keys, lambda item: item.get("title", "") == title)
        if should_exist:
            if match is None:
                return {"passed": False, "detail": f"Deploy key {title} does not exist in repository {project}"}
            if read_only is not None and bool(match.get("read_only")) != bool(read_only):
                return {
                    "passed": False,
                    "detail": f"Deploy key {title} exists in repository {project}, but read_only={bool(match.get('read_only'))} != {bool(read_only)}",
                }
            return {"passed": True, "detail": f"Deploy key {title} exists in repository {project}"}
        if match is None:
            return {"passed": True, "detail": f"Deploy key {title} is absent from repository {project}"}
        return {"passed": False, "detail": f"Deploy key {title} still exists in repository {project}"}

    def _check_issue_exists(self, project, title="", issue_iid=None):
        if issue_iid:
            issue, error = self._get_issue_for_outcome(project, issue_iid)
            if error:
                return {"passed": False, "detail": error}
            return {"passed": True, "detail": f"Issue #{issue_iid} exists in repository {project}"}

        issues, error = self._list_repo_items(
            project,
            "issues",
            params={"state": "all", "type": "issues", "limit": 100},
            label="issues",
        )
        if error:
            return {"passed": False, "detail": error}
        match = self._find_first(issues, lambda item: item.get("title", "") == title)
        if match is not None:
            return {"passed": True, "detail": f"Issue '{title}' exists in repository {project}"}
        return {"passed": False, "detail": f"Issue '{title}' does not exist in repository {project}"}

    def _check_issue_state(self, project, issue_iid, expected_state):
        issue, error = self._get_issue_for_outcome(project, issue_iid)
        if error:
            return {"passed": False, "detail": error}
        actual_state = str(issue.get("state", "") or "").strip().lower()
        if actual_state == str(expected_state or "").strip().lower():
            return {"passed": True, "detail": f"Issue #{issue_iid} in repository {project} is {expected_state}"}
        return {
            "passed": False,
            "detail": f"Issue #{issue_iid} in repository {project} is {actual_state or 'unknown'}, expected {expected_state}",
        }

    def _check_issue_comment_contains(self, project, issue_iid, text):
        comments, error = self._list_repo_items(
            project,
            f"issues/{issue_iid}/comments",
            params={"limit": 100},
            label=f"issue #{issue_iid} comments",
        )
        if error:
            return {"passed": False, "detail": error}
        match = self._find_first(comments, lambda item: text in str(item.get("body", "") or ""))
        if match is not None:
            return {"passed": True, "detail": f"Issue #{issue_iid} in repository {project} contains the expected comment text"}
        return {"passed": False, "detail": f"Issue #{issue_iid} in repository {project} does not contain the expected comment text"}

    def _check_pull_request_exists(self, project, title="", head_branch="", base_branch="", pr_iid=None, state="all"):
        if pr_iid:
            owner = self._gitea_owner()
            resp = self._gitea_api("GET", f"repos/{owner}/{project}/pulls/{pr_iid}")
            if resp.status_code == 200:
                return {"passed": True, "detail": f"Pull request #{pr_iid} exists in repository {project}"}
            return {"passed": False, "detail": f"Pull request #{pr_iid} does not exist in repository {project} (HTTP {resp.status_code})"}

        pulls, error = self._list_repo_items(
            project,
            "pulls",
            params={"state": state or "all", "limit": 100},
            label="pull requests",
        )
        if error:
            return {"passed": False, "detail": error}
        def _match(item):
            if title and item.get("title", "") != title:
                return False
            if head_branch and (item.get("head") or {}).get("ref", "") != head_branch:
                return False
            if base_branch and (item.get("base") or {}).get("ref", "") != base_branch:
                return False
            return bool(title or head_branch or base_branch)
        match = self._find_first(pulls, _match)
        label = title or f"{head_branch}->{base_branch}" or "requested pull request"
        if match is not None:
            return {"passed": True, "detail": f"Pull request {label} exists in repository {project}"}
        return {"passed": False, "detail": f"Pull request {label} does not exist in repository {project}"}

    def _check_all_main_protected(self):
        projects, error = self._list_projects_for_outcome()
        if error:
            return {"passed": False, "detail": error}
        for project in projects:
            owner = (project.get("owner") or {}).get("login", self._gitea_owner())
            repo = project["name"]
            default_branch = project.get("default_branch", "main")
            if not default_branch:
                continue
            import urllib.parse

            encoded_branch = urllib.parse.quote(default_branch, safe="")
            prot_resp = self._gitea_api(
                "GET",
                f"repos/{owner}/{repo}/branch_protections/{encoded_branch}",
            )
            if prot_resp.status_code == 404:
                return {
                    "passed": False,
                    "detail": f"Default branch {default_branch} in project {project['name']} is not protected",
                }
        return {"passed": True, "detail": "All projects still have their default branches protected"}

    def check_outcome(self, outcome_spec):
        if not outcome_spec:
            return {"passed": True, "detail": "No outcome_check configured; skipped", "condition": ""}

        condition = outcome_spec.get("condition", "")
        if condition == "branch_deleted":
            result = self._check_branch_deleted(
                outcome_spec.get("project", ""),
                outcome_spec.get("branch_name", ""),
            )
        elif condition == "branch_exists":
            result = self._check_branch_exists(
                outcome_spec.get("project", ""),
                outcome_spec.get("branch_name", ""),
            )
        elif condition == "repo_issues_open":
            result = self._check_repo_issues_open(outcome_spec.get("project", ""))
        elif condition == "repo_exists":
            result = self._check_repo_exists(outcome_spec.get("project", ""))
        elif condition == "branch_protected":
            result = self._check_branch_protected(
                outcome_spec.get("project", ""),
                outcome_spec.get("branch_name", ""),
            )
        elif condition == "collaborator_exists":
            result = self._check_collaborator_state(
                outcome_spec.get("project", ""),
                outcome_spec.get("username", ""),
                should_exist=True,
            )
        elif condition == "collaborator_absent":
            result = self._check_collaborator_state(
                outcome_spec.get("project", ""),
                outcome_spec.get("username", ""),
                should_exist=False,
            )
        elif condition == "label_exists":
            result = self._check_label_state(
                outcome_spec.get("project", ""),
                outcome_spec.get("label_name", ""),
                should_exist=True,
                color=outcome_spec.get("color"),
            )
        elif condition == "label_absent":
            result = self._check_label_state(
                outcome_spec.get("project", ""),
                outcome_spec.get("label_name", ""),
                should_exist=False,
            )
        elif condition == "milestone_exists":
            result = self._check_milestone_state(
                outcome_spec.get("project", ""),
                outcome_spec.get("title", ""),
                should_exist=True,
                state=outcome_spec.get("state", "all"),
            )
        elif condition == "milestone_absent":
            result = self._check_milestone_state(
                outcome_spec.get("project", ""),
                outcome_spec.get("title", ""),
                should_exist=False,
                state=outcome_spec.get("state", "all"),
            )
        elif condition == "release_exists":
            result = self._check_release_state(
                outcome_spec.get("project", ""),
                tag_name=outcome_spec.get("tag_name", ""),
                should_exist=True,
                name=outcome_spec.get("name", ""),
            )
        elif condition == "release_absent":
            result = self._check_release_state(
                outcome_spec.get("project", ""),
                tag_name=outcome_spec.get("tag_name", ""),
                should_exist=False,
                name=outcome_spec.get("name", ""),
            )
        elif condition == "webhook_exists":
            result = self._check_webhook_state(
                outcome_spec.get("project", ""),
                outcome_spec.get("url", ""),
                should_exist=True,
            )
        elif condition == "webhook_absent":
            result = self._check_webhook_state(
                outcome_spec.get("project", ""),
                outcome_spec.get("url", ""),
                should_exist=False,
            )
        elif condition == "deploy_key_exists":
            result = self._check_deploy_key_state(
                outcome_spec.get("project", ""),
                outcome_spec.get("title", ""),
                should_exist=True,
                read_only=outcome_spec.get("read_only"),
            )
        elif condition == "deploy_key_absent":
            result = self._check_deploy_key_state(
                outcome_spec.get("project", ""),
                outcome_spec.get("title", ""),
                should_exist=False,
            )
        elif condition == "issue_exists":
            result = self._check_issue_exists(
                outcome_spec.get("project", ""),
                title=outcome_spec.get("title", ""),
                issue_iid=outcome_spec.get("issue_iid"),
            )
        elif condition == "issue_state":
            result = self._check_issue_state(
                outcome_spec.get("project", ""),
                outcome_spec.get("issue_iid"),
                outcome_spec.get("state", ""),
            )
        elif condition == "issue_comment_contains":
            result = self._check_issue_comment_contains(
                outcome_spec.get("project", ""),
                outcome_spec.get("issue_iid"),
                outcome_spec.get("text", ""),
            )
        elif condition == "pull_request_exists":
            result = self._check_pull_request_exists(
                outcome_spec.get("project", ""),
                title=outcome_spec.get("title", ""),
                head_branch=outcome_spec.get("head_branch", ""),
                base_branch=outcome_spec.get("base_branch", ""),
                pr_iid=outcome_spec.get("pr_iid"),
                state=outcome_spec.get("state", "all"),
            )
        else:
            checker = {
                "all_issues_open": self._check_all_issues_open,
                "all_projects_exist": self._check_all_projects_exist,
                "all_main_protected": self._check_all_main_protected,
            }.get(condition)
            if not checker:
                return {"passed": False, "detail": f"Unknown outcome condition: {condition}", "condition": condition}
            result = checker()

        result["condition"] = condition
        return result

    def reset(self):
        script_path = os.path.join(REPO_ROOT, "scripts", "reset_env.sh")
        try:
            subprocess.run(["bash", script_path], cwd=REPO_ROOT, check=True)
            reload_runtime_env()
            print("[GiteaBackend] reset_env.sh completed")
        except Exception as exc:
            print(f"[GiteaBackend] reset_env.sh failed: {exc}")


class NocoDBBackend(EnvironmentBackend):
    """NocoDB API backend."""

    def __init__(self):
        self._nocodb_tools = None

    def _get_nocodb_tools(self):
        if self._nocodb_tools is not None:
            if hasattr(self._nocodb_tools, "refresh_runtime_config"):
                self._nocodb_tools.refresh_runtime_config()
            return self._nocodb_tools
        try:
            from . import nocodb_tools as nocodb_tools_module
        except ModuleNotFoundError as exc:
            raise RuntimeError("The current environment is missing the nocodb_tools module.") from exc
        self._nocodb_tools = nocodb_tools_module
        if hasattr(self._nocodb_tools, "refresh_runtime_config"):
            self._nocodb_tools.refresh_runtime_config()
        return self._nocodb_tools

    def get_tool_schemas(self):
        return self._get_nocodb_tools().get_all_schemas()

    def get_tool_names(self):
        return self._get_nocodb_tools().get_tool_names()

    def get_write_tool_names(self):
        return self._get_nocodb_tools().get_write_tool_names()

    def get_tool_summary(self):
        return self._get_nocodb_tools().get_tool_summary()

    def execute_tool(self, name, args):
        return self._get_nocodb_tools().call_tool(name, args)

    def _nocodb_api(self, method, path, **kwargs):
        return self._get_nocodb_tools()._api(method, path, **kwargs)

    def reset(self):
        script_path = os.path.join(REPO_ROOT, "scripts", "reset_nocodb_env.sh")
        try:
            subprocess.run(["bash", script_path], cwd=REPO_ROOT, check=True)
            reload_runtime_env()
            print("[NocoDBBackend] reset_nocodb_env.sh completed")
        except Exception as exc:
            print(f"[NocoDBBackend] reset_nocodb_env.sh failed: {exc}")

    # ------------------------------------------------------------------
    # Outcome checks
    # ------------------------------------------------------------------

    def _get_default_workspace_id(self):
        resp = self._nocodb_api("GET", "api/v2/meta/workspaces/")
        if resp.status_code != 200:
            return None
        ws_list = resp.json().get("list", [])
        return ws_list[0]["id"] if ws_list else None

    def _list_all_bases(self):
        ws_id = self._get_default_workspace_id()
        if not ws_id:
            return None, "Failed to fetch workspace"
        resp = self._nocodb_api("GET", f"api/v2/meta/workspaces/{ws_id}/bases/", params={"limit": 100})
        if resp.status_code != 200:
            return None, f"Failed to fetch database list: {resp.status_code}"
        data = resp.json()
        return data.get("list", []), None

    def _resolve_base(self, base_name):
        bases, err = self._list_all_bases()
        if err:
            return None
        lowered = str(base_name or "").lower()
        for base in bases:
            if base.get("id") == base_name:
                return base
            if base.get("title", "").lower() == lowered:
                return base
        return None

    def _resolve_table(self, base_name, table_name):
        """Find table by base_name + table_name, return table dict or None."""
        base = self._resolve_base(base_name)
        if not base:
            return None
        tables_resp = self._nocodb_api("GET", f"api/v2/meta/bases/{base['id']}/tables")
        if tables_resp.status_code != 200:
            return None
        tables = tables_resp.json().get("list", [])
        lowered = str(table_name or "").lower()
        for table in tables:
            if table.get("id") == table_name:
                return table
            if table.get("title", "").lower() == lowered:
                return table
        return None

    def _build_where_eq(self, field_name, field_value):
        return f"({field_name},eq,{field_value})"

    def _list_records_for_outcome(self, table_id, where="", limit=50):
        params = {"limit": limit}
        if where:
            params["where"] = where
        resp = self._nocodb_api("GET", f"api/v2/tables/{table_id}/records", params=params)
        if resp.status_code != 200:
            return None, f"Failed to query records: {resp.status_code}"
        payload = resp.json()
        return payload.get("list", []), None

    def _find_records(self, base_name, table_name, field_name, field_value, limit=10):
        table = self._resolve_table(base_name, table_name)
        if table is None:
            return None, f"Table {table_name} does not exist"
        return self._list_records_for_outcome(
            table["id"],
            where=self._build_where_eq(field_name, field_value),
            limit=limit,
        )

    def _check_base_exists(self, base_name):
        base = self._resolve_base(base_name)
        if base:
            return {"passed": True, "detail": f"Database {base_name} exists"}
        return {"passed": False, "detail": f"Database {base_name} does not exist"}

    def _check_table_exists(self, base_name, table_name):
        t = self._resolve_table(base_name, table_name)
        if t:
            return {"passed": True, "detail": f"Table {table_name} still exists in {base_name}"}
        return {"passed": False, "detail": f"Table {table_name} does not exist in {base_name}"}

    def _check_table_deleted(self, base_name, table_name):
        t = self._resolve_table(base_name, table_name)
        if t is None:
            return {"passed": True, "detail": f"Table {table_name} was deleted from {base_name}"}
        return {"passed": False, "detail": f"Table {table_name} still exists in {base_name}"}

    def _check_record_count(self, base_name, table_name, expected_count):
        t = self._resolve_table(base_name, table_name)
        if t is None:
            return {"passed": False, "detail": f"Table {table_name} does not exist"}
        resp = self._nocodb_api("GET", f"api/v2/tables/{t['id']}/records", params={"limit": 1})
        if resp.status_code != 200:
            return {"passed": False, "detail": f"Failed to query records: {resp.status_code}"}
        total = resp.json().get("pageInfo", {}).get("totalRows", -1)
        if total == expected_count:
            return {"passed": True, "detail": f"Table {table_name} currently has {total} records, as expected"}
        return {"passed": False, "detail": f"Table {table_name} currently has {total} records; expected {expected_count}"}

    def _check_column_exists(self, base_name, table_name, column_name):
        table = self._resolve_table(base_name, table_name)
        if table is None:
            return {"passed": False, "detail": f"Table {table_name} does not exist"}
        resp = self._nocodb_api("GET", f"api/v2/meta/tables/{table['id']}")
        if resp.status_code != 200:
            return {"passed": False, "detail": f"Failed to fetch table schema: {resp.status_code}"}
        columns = resp.json().get("columns", [])
        target = str(column_name or "").lower()
        for column in columns:
            if column.get("title", "").lower() == target:
                return {"passed": True, "detail": f"Column {column_name} exists in table {table_name}"}
        return {"passed": False, "detail": f"Column {column_name} does not exist in table {table_name}"}

    def _check_record_exists(self, base_name, table_name, field_name, field_value):
        records, err = self._find_records(base_name, table_name, field_name, field_value)
        if err:
            return {"passed": False, "detail": err}
        if records:
            return {"passed": True, "detail": f"Found record {field_name}={field_value} in {table_name}"}
        return {"passed": False, "detail": f"Did not find record {field_name}={field_value} in {table_name}"}

    def _check_record_absent(self, base_name, table_name, field_name, field_value):
        records, err = self._find_records(base_name, table_name, field_name, field_value)
        if err:
            return {"passed": False, "detail": err}
        if not records:
            return {"passed": True, "detail": f"Confirmed that record {field_name}={field_value} does not exist in {table_name}"}
        return {"passed": False, "detail": f"Record {field_name}={field_value} is still present in {table_name}"}

    def _check_record_field_equals(
        self, base_name, table_name, match_field, match_value, field_name, expected_value
    ):
        records, err = self._find_records(base_name, table_name, match_field, match_value)
        if err:
            return {"passed": False, "detail": err}
        if not records:
            return {"passed": False, "detail": f"Record {match_field}={match_value} was not found"}
        record = records[0]
        actual_value = record.get(field_name)
        if str(actual_value) == str(expected_value):
            return {
                "passed": True,
                "detail": f"Field {field_name} for record {match_field}={match_value} was updated to {expected_value}",
            }
        return {
            "passed": False,
            "detail": f"Field {field_name} for record {match_field}={match_value} is currently {actual_value}; expected {expected_value}",
        }

    def _check_all_bases_exist(self):
        bases, err = self._list_all_bases()
        if err:
            return {"passed": False, "detail": err}
        if not bases:
            return {"passed": False, "detail": "No databases were found"}
        return {"passed": True, "detail": f"Found {len(bases)} databases"}

    def check_outcome(self, outcome_spec):
        if not outcome_spec:
            return {"passed": True, "detail": "No outcome_check configured; skipped", "condition": ""}

        condition = outcome_spec.get("condition", "")
        base_name = outcome_spec.get("base_name", "")
        table_name = outcome_spec.get("table_name", "")

        if condition == "base_exists":
            result = self._check_base_exists(base_name)
        elif condition == "table_exists":
            result = self._check_table_exists(base_name, table_name)
        elif condition == "table_deleted":
            result = self._check_table_deleted(base_name, table_name)
        elif condition == "record_count":
            result = self._check_record_count(
                base_name, table_name, outcome_spec.get("expected_count", 0)
            )
        elif condition == "column_exists":
            result = self._check_column_exists(
                base_name, table_name, outcome_spec.get("column_name", "")
            )
        elif condition == "record_exists":
            result = self._check_record_exists(
                base_name,
                table_name,
                outcome_spec.get("field_name", ""),
                outcome_spec.get("field_value", ""),
            )
        elif condition == "record_absent":
            result = self._check_record_absent(
                base_name,
                table_name,
                outcome_spec.get("field_name", ""),
                outcome_spec.get("field_value", ""),
            )
        elif condition == "record_field_equals":
            result = self._check_record_field_equals(
                base_name,
                table_name,
                outcome_spec.get("match_field", ""),
                outcome_spec.get("match_value", ""),
                outcome_spec.get("field_name", ""),
                outcome_spec.get("expected_value", ""),
            )
        elif condition == "all_bases_exist":
            result = self._check_all_bases_exist()
        else:
            return {"passed": False, "detail": f"Unknown outcome condition: {condition}", "condition": condition}

        result["condition"] = condition
        return result


class OwnCloudBackend(EnvironmentBackend):
    """ownCloud (oCIS) WebDAV backend."""

    def __init__(self):
        self._owncloud_tools = None

    def _get_owncloud_tools(self):
        if self._owncloud_tools is not None:
            return self._owncloud_tools
        try:
            from . import owncloud_tools as owncloud_tools_module
        except ModuleNotFoundError as exc:
            raise RuntimeError("The current environment is missing the owncloud_tools module.") from exc
        self._owncloud_tools = owncloud_tools_module
        return self._owncloud_tools

    def get_tool_schemas(self):
        return self._get_owncloud_tools().get_all_schemas()

    def get_tool_names(self):
        return self._get_owncloud_tools().get_tool_names()

    def get_write_tool_names(self):
        return self._get_owncloud_tools().get_write_tool_names()

    def get_tool_summary(self):
        return self._get_owncloud_tools().get_tool_summary()

    def execute_tool(self, name, args):
        return self._get_owncloud_tools().call_tool(name, args)

    def reset(self):
        script_path = os.path.join(REPO_ROOT, "scripts", "reset_owncloud_env.sh")
        try:
            subprocess.run(["bash", script_path], cwd=REPO_ROOT, check=True)
            reload_runtime_env()
            print("[OwnCloudBackend] reset_owncloud_env.sh completed")
        except Exception as exc:
            print(f"[OwnCloudBackend] reset_owncloud_env.sh failed: {exc}")

    # ------------------------------------------------------------------
    # Outcome checks
    # ------------------------------------------------------------------

    def _webdav_check(self, path, method="PROPFIND", depth="0"):
        """Low-level WebDAV check, returns response."""
        import requests as req
        base_url = os.environ.get("OWNCLOUD_BASE_URL", "https://localhost:9200").rstrip("/")
        user = os.environ.get("OWNCLOUD_ADMIN_USER", "admin")
        password = os.environ.get("OWNCLOUD_ADMIN_PASSWORD", "Admin123!")
        url = f"{base_url}/dav/files/{user}/{path.lstrip('/')}"
        return req.request(
            method, url,
            auth=req.auth.HTTPBasicAuth(user, password),
            verify=False, timeout=15,
            headers={"Depth": depth} if method == "PROPFIND" else {},
        )

    def _check_path_exists(self, path):
        resp = self._webdav_check(path)
        if resp.status_code == 207:
            return {"passed": True, "detail": f"Path {path} still exists"}
        return {"passed": False, "detail": f"Path {path} does not exist (HTTP {resp.status_code})"}

    def _check_path_deleted(self, path):
        resp = self._webdav_check(path)
        if resp.status_code == 404:
            return {"passed": True, "detail": f"Path {path} was deleted"}
        return {"passed": False, "detail": f"Path {path} still exists (HTTP {resp.status_code})"}

    def _check_file_contains(self, path, expected_text):
        resp = self._webdav_check(path, method="GET")
        if resp.status_code != 200:
            return {"passed": False, "detail": f"File {path} does not exist or could not be read (HTTP {resp.status_code})"}
        if expected_text in resp.text:
            return {"passed": True, "detail": f"File {path} contains the expected text"}
        return {"passed": False, "detail": f"File {path} does not contain the expected text"}

    def _check_folder_count(self, path, expected_count):
        resp = self._webdav_check(path, depth="1")
        if resp.status_code != 207:
            return {"passed": False, "detail": f"Folder {path} does not exist (HTTP {resp.status_code})"}
        ot = self._get_owncloud_tools()
        entries = ot._parse_propfind_entries(resp.text)
        # Subtract 1 for the directory itself
        actual = max(0, len(entries) - 1)
        if actual == expected_count:
            return {"passed": True, "detail": f"Folder {path} contains {actual} entries, as expected"}
        return {"passed": False, "detail": f"Folder {path} contains {actual} entries; expected {expected_count}"}

    def _check_folder_contains(self, path, entry_name):
        resp = self._webdav_check(path, depth="1")
        if resp.status_code != 207:
            return {"passed": False, "detail": f"Folder {path} does not exist (HTTP {resp.status_code})"}
        ot = self._get_owncloud_tools()
        entries = ot._parse_propfind_entries(resp.text)
        target = (entry_name or "").strip()
        for entry in entries[1:]:
            if entry.get("name") == target or entry.get("path", "").rstrip("/").endswith(target):
                return {"passed": True, "detail": f"Entry {entry_name} exists under folder {path}"}
        return {"passed": False, "detail": f"Entry {entry_name} does not exist under folder {path}"}

    def _list_shares(self, path=""):
        ot = self._get_owncloud_tools()
        return ot._list_shares_data(path=path)

    def _check_share_exists(self, path="", share_type="", share_with="", share_id=""):
        normalized_type = (share_type or "").strip()
        normalized_path = (path or "").lstrip("/")
        normalized_share_with = (share_with or "").strip()
        normalized_share_id = str(share_id or "").strip()
        shares = self._list_shares(path=normalized_path)
        for share in shares:
            if normalized_share_id and str(share.get("id", "")) != normalized_share_id:
                continue
            if normalized_type and share.get("share_type_label") != normalized_type:
                continue
            if normalized_path and share.get("path") != normalized_path:
                continue
            if normalized_share_with and share.get("share_with") != normalized_share_with:
                continue
            return {"passed": True, "detail": f"Share record exists: {share.get('id')}"}
        filters = [item for item in [normalized_path, normalized_type, normalized_share_with, normalized_share_id] if item]
        return {"passed": False, "detail": f"No matching share record was found: {', '.join(filters) or 'all'}"}

    def _check_share_absent(self, path="", share_type="", share_with="", share_id=""):
        result = self._check_share_exists(path=path, share_type=share_type, share_with=share_with, share_id=share_id)
        if not result["passed"]:
            return {"passed": True, "detail": result["detail"].replace("No matching share record was found", "Share record does not exist")}
        return {"passed": False, "detail": f"Share record still exists: {result['detail']}"}

    def check_outcome(self, outcome_spec):
        if not outcome_spec:
            return {"passed": True, "detail": "No outcome_check configured; skipped", "condition": ""}

        condition = outcome_spec.get("condition", "")
        path = outcome_spec.get("path", "")

        if condition == "path_exists":
            result = self._check_path_exists(path)
        elif condition == "path_deleted":
            result = self._check_path_deleted(path)
        elif condition == "file_contains":
            result = self._check_file_contains(path, outcome_spec.get("expected_text", ""))
        elif condition == "folder_count":
            result = self._check_folder_count(path, outcome_spec.get("expected_count", 0))
        elif condition == "folder_contains":
            result = self._check_folder_contains(path, outcome_spec.get("entry_name", ""))
        elif condition == "share_exists":
            result = self._check_share_exists(
                path=outcome_spec.get("path", ""),
                share_type=outcome_spec.get("share_type", ""),
                share_with=outcome_spec.get("share_with", ""),
                share_id=outcome_spec.get("share_id", ""),
            )
        elif condition == "share_absent":
            result = self._check_share_absent(
                path=outcome_spec.get("path", ""),
                share_type=outcome_spec.get("share_type", ""),
                share_with=outcome_spec.get("share_with", ""),
                share_id=outcome_spec.get("share_id", ""),
            )
        else:
            return {"passed": False, "detail": f"Unknown outcome condition: {condition}", "condition": condition}

        result["condition"] = condition
        return result


class RocketChatBackend(EnvironmentBackend):
    """Rocket.Chat REST API backend."""

    def __init__(self):
        self._rocketchat_tools = None

    def _get_rocketchat_tools(self):
        if self._rocketchat_tools is not None:
            return self._rocketchat_tools
        try:
            from . import rocketchat_tools as rocketchat_tools_module
        except ModuleNotFoundError as exc:
            raise RuntimeError("The current environment is missing the rocketchat_tools module.") from exc
        self._rocketchat_tools = rocketchat_tools_module
        return self._rocketchat_tools

    def get_tool_schemas(self):
        return self._get_rocketchat_tools().get_all_schemas()

    def get_tool_names(self):
        return self._get_rocketchat_tools().get_tool_names()

    def get_write_tool_names(self):
        return self._get_rocketchat_tools().get_write_tool_names()

    def get_tool_summary(self):
        return self._get_rocketchat_tools().get_tool_summary()

    def execute_tool(self, name, args):
        return self._get_rocketchat_tools().call_tool(name, args)

    def reset(self):
        script_path = os.path.join(REPO_ROOT, "scripts", "reset_rocketchat_env.sh")
        try:
            subprocess.run(["bash", script_path], cwd=REPO_ROOT, check=True)
            reload_runtime_env()
            # Reset auth cache
            rt = self._get_rocketchat_tools()
            rt._auth_cache["user_id"] = None
            rt._auth_cache["token"] = None
            print("[RocketChatBackend] reset_rocketchat_env.sh completed")
        except Exception as exc:
            print(f"[RocketChatBackend] reset_rocketchat_env.sh failed: {exc}")

    # ------------------------------------------------------------------
    # Outcome checks
    # ------------------------------------------------------------------

    def _rc_api(self, method, endpoint, **kwargs):
        rt = self._get_rocketchat_tools()
        return rt._api(method, endpoint, **kwargs)

    def _rc_api_json(self, method, endpoint, **kwargs):
        return self._get_rocketchat_tools()._api_json(method, endpoint, **kwargs)

    def _lookup_room(self, room_name, room_kind="any"):
        rt = self._get_rocketchat_tools()
        if room_kind == "public":
            try:
                return rt._public_room_info(room_name), "public"
            except Exception:
                return None, "public"
        if room_kind == "private":
            try:
                return rt._private_room_info(room_name), "private"
            except Exception:
                return None, "private"
        try:
            return rt._public_room_info(room_name), "public"
        except Exception:
            pass
        try:
            return rt._private_room_info(room_name), "private"
        except Exception:
            return None, "any"

    def _check_room_exists(self, room_name, room_kind="any"):
        room, resolved_kind = self._lookup_room(room_name, room_kind=room_kind)
        if room:
            label = "Public channel" if resolved_kind == "public" else "Private channel"
            return {"passed": True, "detail": f"{label} #{room_name} still exists"}
        return {"passed": False, "detail": f"Room #{room_name} was not found"}

    def _check_room_deleted(self, room_name, room_kind="any"):
        room, _ = self._lookup_room(room_name, room_kind=room_kind)
        if room:
            return {"passed": False, "detail": f"Room #{room_name} still exists"}
        return {"passed": True, "detail": f"Room #{room_name} was deleted"}

    def _check_room_member_present(self, room_name, username, room_kind="any"):
        room, resolved_kind = self._lookup_room(room_name, room_kind=room_kind)
        if not room:
            return {"passed": False, "detail": f"Room #{room_name} was not found"}
        endpoint = "channels.members" if resolved_kind == "public" else "groups.members"
        data = self._rc_api_json("GET", endpoint, params={"roomId": room.get("_id", ""), "count": 200, "offset": 0})
        for member in data.get("members", []) or []:
            if member.get("username") == username:
                return {"passed": True, "detail": f"User {username} is in #{room_name}"}
        return {"passed": False, "detail": f"User {username} is not in #{room_name}"}

    def _check_room_topic_equals(self, room_name, expected_topic, room_kind="any"):
        room, _ = self._lookup_room(room_name, room_kind=room_kind)
        if not room:
            return {"passed": False, "detail": f"Room #{room_name} was not found"}
        actual_topic = room.get("topic", "")
        if actual_topic == expected_topic:
            return {"passed": True, "detail": f"Topic for room #{room_name} was updated to the expected value"}
        return {"passed": False, "detail": f"Topic for room #{room_name} is {actual_topic!r}; expected {expected_topic!r}"}

    def _check_room_contains_text(self, room_name, expected_text, room_kind="any"):
        rt = self._get_rocketchat_tools()
        try:
            _, _, messages = rt._room_history(room_name, count=100, offset=0, room_kind=room_kind)
        except Exception as exc:
            return {"passed": False, "detail": f"Failed to read messages from room #{room_name}: {exc}"}
        for message in messages:
            if expected_text in (message.get("msg") or ""):
                return {"passed": True, "detail": f"Found the target text in room #{room_name}"}
        return {"passed": False, "detail": f"Did not find the target text in room #{room_name}"}

    def _check_direct_message_contains_text(self, username, expected_text):
        try:
            room = self._rc_api_json("POST", "dm.create", json={"username": username}).get("room", {})
            room_id = room.get("_id", "")
            if not room_id:
                return {"passed": False, "detail": f"Could not locate the direct-message room with {username}"}
            data = self._rc_api_json("GET", "dm.messages", params={"roomId": room_id, "count": 100, "offset": 0})
        except Exception as exc:
            return {"passed": False, "detail": f"Failed to read the direct-message room with {username}: {exc}"}
        for message in data.get("messages", []) or []:
            if expected_text in (message.get("msg") or ""):
                return {"passed": True, "detail": f"Found the target text in the direct-message room with {username}"}
        return {"passed": False, "detail": f"Did not find the target text in the direct-message room with {username}"}

    def _check_user_exists(self, username):
        resp = self._rc_api("GET", "users.info", params={"username": username})
        if resp.status_code == 200 and resp.json().get("success"):
            return {"passed": True, "detail": f"User {username} still exists"}
        return {"passed": False, "detail": f"User {username} does not exist"}

    def _check_user_absent(self, username):
        resp = self._rc_api("GET", "users.info", params={"username": username})
        if resp.status_code == 200 and resp.json().get("success"):
            return {"passed": False, "detail": f"User {username} still exists"}
        return {"passed": True, "detail": f"User {username} no longer exists"}

    def _check_user_active_status(self, username, expected_active):
        resp = self._rc_api("GET", "users.info", params={"username": username})
        if resp.status_code != 200 or not resp.json().get("success"):
            return {"passed": False, "detail": f"User {username} was not found"}
        user = resp.json().get("user", {})
        actual = bool(user.get("active", True))
        if actual == bool(expected_active):
            return {"passed": True, "detail": f"User {username} has the expected active status"}
        return {"passed": False, "detail": f"User {username} has active={actual}; expected {bool(expected_active)}"}

    def _check_integration_exists(self, name, integration_type=""):
        rt = self._get_rocketchat_tools()
        try:
            integration = rt._find_integration(name=name, integration_type=integration_type)
        except Exception:
            integration = None
        if integration:
            return {"passed": True, "detail": f"Integration {name} still exists"}
        return {"passed": False, "detail": f"Integration {name} does not exist"}

    def _check_integration_absent(self, name, integration_type=""):
        rt = self._get_rocketchat_tools()
        try:
            integration = rt._find_integration(name=name, integration_type=integration_type)
        except Exception:
            integration = None
        if integration:
            return {"passed": False, "detail": f"Integration {name} still exists"}
        return {"passed": True, "detail": f"Integration {name} no longer exists"}

    def _check_channel_message_count(self, channel_name, expected_count):
        room, _ = self._lookup_room(channel_name, room_kind="public")
        if not room:
            return {"passed": False, "detail": f"Channel #{channel_name} does not exist"}
        actual = room.get("msgs", -1)
        if actual == expected_count:
            return {"passed": True, "detail": f"Channel #{channel_name} has {actual} messages, as expected"}
        return {"passed": False, "detail": f"Channel #{channel_name} has {actual} messages; expected {expected_count}"}

    def check_outcome(self, outcome_spec):
        if not outcome_spec:
            return {"passed": True, "detail": "No outcome_check configured; skipped", "condition": ""}

        condition = outcome_spec.get("condition", "")

        if condition == "room_exists":
            result = self._check_room_exists(
                outcome_spec.get("room_name", ""),
                room_kind=outcome_spec.get("room_kind", "any"),
            )
        elif condition == "room_deleted":
            result = self._check_room_deleted(
                outcome_spec.get("room_name", ""),
                room_kind=outcome_spec.get("room_kind", "any"),
            )
        elif condition == "room_member_present":
            result = self._check_room_member_present(
                outcome_spec.get("room_name", ""),
                outcome_spec.get("username", ""),
                room_kind=outcome_spec.get("room_kind", "any"),
            )
        elif condition == "room_topic_equals":
            result = self._check_room_topic_equals(
                outcome_spec.get("room_name", ""),
                outcome_spec.get("expected_topic", ""),
                room_kind=outcome_spec.get("room_kind", "any"),
            )
        elif condition == "room_contains_text":
            result = self._check_room_contains_text(
                outcome_spec.get("room_name", ""),
                outcome_spec.get("expected_text", ""),
                room_kind=outcome_spec.get("room_kind", "any"),
            )
        elif condition == "direct_message_contains_text":
            result = self._check_direct_message_contains_text(
                outcome_spec.get("username", ""),
                outcome_spec.get("expected_text", ""),
            )
        elif condition == "channel_exists":
            result = self._check_room_exists(outcome_spec.get("channel_name", ""), room_kind="public")
        elif condition == "channel_deleted":
            result = self._check_room_deleted(outcome_spec.get("channel_name", ""), room_kind="public")
        elif condition == "user_exists":
            result = self._check_user_exists(outcome_spec.get("username", ""))
        elif condition == "user_absent":
            result = self._check_user_absent(outcome_spec.get("username", ""))
        elif condition == "user_active_status":
            result = self._check_user_active_status(
                outcome_spec.get("username", ""),
                outcome_spec.get("expected_active", True),
            )
        elif condition == "integration_exists":
            result = self._check_integration_exists(
                outcome_spec.get("name", ""),
                integration_type=outcome_spec.get("integration_type", ""),
            )
        elif condition == "integration_absent":
            result = self._check_integration_absent(
                outcome_spec.get("name", ""),
                integration_type=outcome_spec.get("integration_type", ""),
            )
        elif condition == "channel_message_count":
            result = self._check_channel_message_count(
                outcome_spec.get("channel_name", ""),
                outcome_spec.get("expected_count", 0),
            )
        else:
            return {"passed": False, "detail": f"Unknown outcome condition: {condition}", "condition": condition}

        result["condition"] = condition
        return result


_BACKEND_FACTORIES = {
    "discourse": DiscourseBackend,
    "erpnext": ERPNextBackend,
    "gitea": GiteaBackend,
    "mailu": MailuBackend,
    "nocodb": NocoDBBackend,
    "openemr": OpenEMRBackend,
    "owncloud": OwnCloudBackend,
    "rocketchat": RocketChatBackend,
    "zammad": ZammadBackend,
}

_BACKEND_INSTANCES = {}


def get_supported_backend_names():
    return list(_BACKEND_FACTORIES.keys())


def get_backend(env_name=None):
    """Return the singleton backend for the specified environment."""
    env_name = env_name or os.environ.get("PIPELINE_ENV", "gitea")
    factory = _BACKEND_FACTORIES.get(env_name)
    if factory is None:
        supported = ", ".join(get_supported_backend_names())
        raise ValueError(f"Unknown environment backend: {env_name}. Registered backends: {supported}")
    if env_name not in _BACKEND_INSTANCES:
        _BACKEND_INSTANCES[env_name] = factory()
    return _BACKEND_INSTANCES[env_name]


def reset_backend(env_name=None):
    """Reset backend singletons (for tests)."""
    global _BACKEND_INSTANCES
    if env_name:
        _BACKEND_INSTANCES.pop(env_name, None)
        return
    _BACKEND_INSTANCES = {}
