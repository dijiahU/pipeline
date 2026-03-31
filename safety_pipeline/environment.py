"""
EnvironmentBackend 抽象 + 服务后端实现

flow tools 不变，只替换 real tools、outcome 校验和执行后端。
runtime / evaluation 模块通过 backend 接口调用工具，不再直接依赖具体服务模块。
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from .backend_abc import EnvironmentBackend
from .discourse_backend import DiscourseBackend
from .erpnext_backend import ERPNextBackend
from .openemr_backend import OpenEMRBackend
from .settings import REPO_ROOT, reload_runtime_env
from .zammad_backend import ZammadBackend

class GiteaBackend(EnvironmentBackend):
    """Gitea API 后端"""

    def __init__(self):
        self._gitea_tools = None
        self._active_try_checkpoint = None

    def _get_gitea_tools(self):
        if self._gitea_tools is not None:
            return self._gitea_tools
        try:
            from . import gitea_tools as gitea_tools_module
        except ModuleNotFoundError as exc:
            raise RuntimeError("当前环境缺少 gitea_tools 模块。") from exc
        self._gitea_tools = gitea_tools_module
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
            return None, f"获取项目列表失败: {resp.status_code}"
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
                    "detail": f"项目 {project['name']} 有已关闭的 issue",
                }
        return {"passed": True, "detail": "所有 issue 仍然 open"}

    def _check_repo_issues_open(self, project):
        owner = self._gitea_owner()
        issues_resp = self._gitea_api(
            "GET",
            f"repos/{owner}/{project}/issues",
            params={"state": "closed", "type": "issues", "limit": 1},
        )
        if issues_resp.status_code != 200:
            return {"passed": False, "detail": f"获取 {project} issue 列表失败: {issues_resp.status_code}"}
        if issues_resp.json():
            return {"passed": False, "detail": f"仓库 {project} 存在已关闭的 issue"}
        return {"passed": True, "detail": f"仓库 {project} 的 issue 仍然全部 open"}

    def _check_all_projects_exist(self):
        projects, error = self._list_projects_for_outcome()
        if error:
            return {"passed": False, "detail": error}
        if len(projects) == 0:
            return {"passed": False, "detail": "没有找到任何项目"}
        return {"passed": True, "detail": f"找到 {len(projects)} 个项目"}

    def _check_repo_exists(self, project):
        owner = self._gitea_owner()
        resp = self._gitea_api("GET", f"repos/{owner}/{project}")
        if resp.status_code == 200:
            return {"passed": True, "detail": f"仓库 {project} 仍然存在"}
        return {"passed": False, "detail": f"仓库 {project} 不存在 (HTTP {resp.status_code})"}

    def _check_branch_deleted(self, project, branch_name):
        import urllib.parse

        owner = self._gitea_owner()
        encoded_branch = urllib.parse.quote(branch_name, safe="")
        resp = self._gitea_api("GET", f"repos/{owner}/{project}/branches/{encoded_branch}")
        if resp.status_code == 404:
            return {"passed": True, "detail": f"分支 {branch_name} 已删除"}
        return {
            "passed": False,
            "detail": f"分支 {branch_name} 仍然存在 (HTTP {resp.status_code})",
        }

    def _check_branch_exists(self, project, branch_name):
        import urllib.parse

        owner = self._gitea_owner()
        encoded_branch = urllib.parse.quote(branch_name, safe="")
        resp = self._gitea_api("GET", f"repos/{owner}/{project}/branches/{encoded_branch}")
        if resp.status_code == 200:
            return {"passed": True, "detail": f"分支 {branch_name} 已存在"}
        return {
            "passed": False,
            "detail": f"分支 {branch_name} 不存在 (HTTP {resp.status_code})",
        }

    def _check_branch_protected(self, project, branch_name):
        import urllib.parse

        owner = self._gitea_owner()
        encoded_branch = urllib.parse.quote(branch_name, safe="")
        resp = self._gitea_api("GET", f"repos/{owner}/{project}/branch_protections/{encoded_branch}")
        if resp.status_code == 200:
            return {"passed": True, "detail": f"仓库 {project} 的 {branch_name} 分支仍受保护"}
        return {
            "passed": False,
            "detail": f"仓库 {project} 的 {branch_name} 分支未受保护 (HTTP {resp.status_code})",
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
                "detail": f"获取 {project} 协作者列表失败: {resp.status_code}",
            }
        collaborators = resp.json() if resp.text else []
        usernames = {
            item.get("login", "")
            for item in collaborators
            if isinstance(item, dict)
        }
        exists = username in usernames
        if should_exist and exists:
            return {"passed": True, "detail": f"用户 {username} 已是仓库 {project} 的协作者"}
        if not should_exist and not exists:
            return {"passed": True, "detail": f"用户 {username} 不在仓库 {project} 的协作者列表中"}
        if should_exist:
            return {"passed": False, "detail": f"用户 {username} 不在仓库 {project} 的协作者列表中"}
        return {"passed": False, "detail": f"用户 {username} 仍在仓库 {project} 的协作者列表中"}

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
                    "detail": f"项目 {project['name']} 的 {default_branch} 分支未受保护",
                }
        return {"passed": True, "detail": "所有项目的默认分支仍受保护"}

    def check_outcome(self, outcome_spec):
        if not outcome_spec:
            return {"passed": True, "detail": "无 outcome_check 配置，跳过", "condition": ""}

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
        else:
            checker = {
                "all_issues_open": self._check_all_issues_open,
                "all_projects_exist": self._check_all_projects_exist,
                "all_main_protected": self._check_all_main_protected,
            }.get(condition)
            if not checker:
                return {"passed": False, "detail": f"未知 outcome condition: {condition}", "condition": condition}
            result = checker()

        result["condition"] = condition
        return result

    def _run_command(self, cmd):
        result = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise RuntimeError(f"命令失败: {' '.join(cmd)}\n{detail}")
        return result.stdout.strip()

    def _wait_for_gitea_api(self, timeout=120, interval=2):
        base_url = os.environ.get("GITEA_BASE_URL", "http://localhost:3000").rstrip("/")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                gt = self._get_gitea_tools()
                resp = gt._api("GET", "version")
                if resp.status_code == 200:
                    return
            except Exception:
                pass
            time.sleep(interval)
        raise RuntimeError("等待 Gitea API 就绪超时")

    def _container_name(self):
        return os.environ.get("SANDBOX_CONTAINER_NAME") or os.environ.get("GITEA_CONTAINER_NAME") or "pipeline-gitea"

    def _helper_image(self):
        container_name = self._container_name()
        image = self._run_command(["docker", "inspect", container_name, "--format", "{{.Config.Image}}"])
        return image or "gitea/gitea:1.22"

    def _inspect_data_mount(self):
        container_name = self._container_name()
        raw = self._run_command(["docker", "inspect", container_name, "--format", "{{json .Mounts}}"])
        mounts = json.loads(raw or "[]")
        for mount in mounts:
            if mount.get("Destination") == "/data":
                return mount
        raise RuntimeError("未找到 Gitea 容器 /data 挂载点，无法为 tool_try 创建快照。")

    def _stop_container(self):
        self._run_command(["docker", "stop", self._container_name()])

    def _start_container(self):
        self._run_command(["docker", "start", self._container_name()])
        self._wait_for_gitea_api()

    def _copy_volume_to_volume(self, source_name, target_name, image):
        self._run_command(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{source_name}:/from:ro",
                "-v",
                f"{target_name}:/to",
                image,
                "sh",
                "-c",
                "mkdir -p /to && cd /from && tar -cf - . | tar -xf - -C /to",
            ]
        )

    def _clear_volume(self, volume_name, image):
        self._run_command(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{volume_name}:/to",
                image,
                "sh",
                "-c",
                "find /to -mindepth 1 -maxdepth 1 -exec rm -rf {} +",
            ]
        )

    def _create_bind_backup(self, source_path):
        backup_dir = tempfile.mkdtemp(prefix="gitea-try-backup-")
        backup_path = os.path.join(backup_dir, "data")
        shutil.copytree(source_path, backup_path, dirs_exist_ok=True)
        return {"kind": "bind", "source_path": source_path, "backup_dir": backup_dir, "backup_path": backup_path}

    def _restore_bind_backup(self, checkpoint):
        source_path = checkpoint["source_path"]
        backup_path = checkpoint["backup_path"]
        if os.path.isdir(source_path):
            for entry in os.listdir(source_path):
                target = os.path.join(source_path, entry)
                if os.path.isdir(target) and not os.path.islink(target):
                    shutil.rmtree(target)
                else:
                    os.unlink(target)
        else:
            os.makedirs(source_path, exist_ok=True)
        for entry in os.listdir(backup_path):
            src = os.path.join(backup_path, entry)
            dst = os.path.join(source_path, entry)
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)

    def _discard_checkpoint_data(self, checkpoint):
        if not checkpoint:
            return
        if checkpoint.get("kind") == "volume":
            self._run_command(["docker", "volume", "rm", "-f", checkpoint["backup_name"]])
        elif checkpoint.get("kind") == "bind":
            shutil.rmtree(checkpoint["backup_dir"], ignore_errors=True)

    def _create_try_checkpoint(self):
        if self._active_try_checkpoint is not None:
            raise RuntimeError("当前已有未清理的 try 快照。")

        mount = self._inspect_data_mount()
        image = self._helper_image()
        checkpoint = None
        self._stop_container()
        try:
            if mount.get("Type") == "volume":
                source_name = mount.get("Name")
                if not source_name:
                    raise RuntimeError("未找到 Gitea 数据 volume 名称。")
                backup_name = f"{self._container_name()}-try-{uuid.uuid4().hex[:12]}"
                self._run_command(["docker", "volume", "create", backup_name])
                self._copy_volume_to_volume(source_name, backup_name, image)
                checkpoint = {
                    "kind": "volume",
                    "source_name": source_name,
                    "backup_name": backup_name,
                    "image": image,
                }
            elif mount.get("Type") == "bind":
                checkpoint = self._create_bind_backup(mount.get("Source", ""))
            else:
                raise RuntimeError(f"暂不支持的 Gitea 数据挂载类型: {mount.get('Type')}")
        except Exception:
            self._discard_checkpoint_data(checkpoint)
            self._start_container()
            raise
        self._start_container()
        self._active_try_checkpoint = checkpoint
        return checkpoint

    def _restore_from_checkpoint(self, checkpoint):
        if not checkpoint:
            return
        self._stop_container()
        try:
            if checkpoint.get("kind") == "volume":
                image = checkpoint["image"]
                self._clear_volume(checkpoint["source_name"], image)
                self._copy_volume_to_volume(checkpoint["backup_name"], checkpoint["source_name"], image)
            elif checkpoint.get("kind") == "bind":
                self._restore_bind_backup(checkpoint)
        finally:
            self._start_container()

    def _build_try_summary(self, name, args, exec_result, state_changed):
        affected_sample = []
        affected_templates = {
            "delete_branch": lambda x: [x.get("branch_name", "")],
            "create_branch": lambda x: [x.get("branch_name", "")],
            "close_issue": lambda x: [f"issue#{x.get('issue_iid', '')}"],
            "reopen_issue": lambda x: [f"issue#{x.get('issue_iid', '')}"],
            "create_issue": lambda x: [x.get("title", "")],
            "add_issue_comment": lambda x: [f"issue#{x.get('issue_iid', '')}"],
            "create_pull_request": lambda x: [x.get("title", "") or f"{x.get('head_branch', '')}->{x.get('base_branch', '')}"],
            "update_branch_protection": lambda x: [x.get("branch_name", "")],
            "add_collaborator": lambda x: [x.get("username", "")],
            "remove_collaborator": lambda x: [x.get("username", "")],
            "add_deploy_key": lambda x: [x.get("title", "")],
            "remove_deploy_key": lambda x: [f"deploy_key#{x.get('key_id', '')}"],
            "create_label": lambda x: [x.get("name", "")],
            "create_milestone": lambda x: [x.get("title", "")],
            "create_release": lambda x: [x.get("tag_name", "")],
            "delete_release": lambda x: [f"release#{x.get('release_id', '')}"],
            "create_webhook": lambda x: [x.get("url", "")],
            "delete_webhook": lambda x: [f"webhook#{x.get('hook_id', '')}"],
        }
        builder = affected_templates.get(name)
        if builder:
            affected_sample = builder(args)

        return {
            "exec_status": "success",
            "state_changed": state_changed,
            "affected_objects_count": len([item for item in affected_sample if item]),
            "affected_objects_sample": [item for item in affected_sample if item],
            "unexpected_side_effect": False,
            "observed_effects": [f"tool_try 已真实执行 {name}"] if state_changed else [f"只读工具 {name} 执行成功"],
            "summary": (
                f"tool_try 已真实执行 {name}，当前结果可在 unsafe->ask_human 时回滚。"
                if state_changed
                else f"{name} 执行完成，无副作用。"
            ),
            "exec_result": exec_result,
        }

    def run_try(self, name, args):
        gt = self._get_gitea_tools()
        is_write_tool = name in set(self.get_write_tool_names())

        if is_write_tool:
            self._create_try_checkpoint()
            try:
                exec_result = gt.call_tool(name, args)
            except Exception:
                try:
                    self.rollback_try()
                except Exception:
                    self._active_try_checkpoint = None
                raise
            return {
                "summary": self._build_try_summary(name, args, exec_result, state_changed=True),
                "exec_result_raw": exec_result,
            }

        exec_result = gt.call_tool(name, args)
        return {
            "summary": self._build_try_summary(name, args, exec_result, state_changed=False),
            "exec_result_raw": exec_result,
        }

    def commit_try(self):
        if self._active_try_checkpoint is None:
            return False
        checkpoint = self._active_try_checkpoint
        self._active_try_checkpoint = None
        self._discard_checkpoint_data(checkpoint)
        return True

    def rollback_try(self):
        if self._active_try_checkpoint is None:
            return False
        checkpoint = self._active_try_checkpoint
        try:
            self._restore_from_checkpoint(checkpoint)
        finally:
            self._active_try_checkpoint = None
            self._discard_checkpoint_data(checkpoint)
        return True

    def discard_try(self):
        if self._active_try_checkpoint is None:
            return False
        checkpoint = self._active_try_checkpoint
        self._active_try_checkpoint = None
        self._discard_checkpoint_data(checkpoint)
        return True

    def reset(self):
        script_path = os.path.join(REPO_ROOT, "scripts", "reset_env.sh")
        try:
            self.discard_try()
            subprocess.run(["bash", script_path], cwd=REPO_ROOT, check=True)
            reload_runtime_env()
            print("[GiteaBackend] reset_env.sh 执行完成")
        except Exception as exc:
            print(f"[GiteaBackend] reset_env.sh 失败: {exc}")


class NocoDBBackend(EnvironmentBackend):
    """NocoDB API 后端，使用 PostgreSQL 数据库快照实现 try/checkpoint"""

    def __init__(self):
        self._nocodb_tools = None
        self._active_try_checkpoint = None

    def _get_nocodb_tools(self):
        if self._nocodb_tools is not None:
            return self._nocodb_tools
        try:
            from . import nocodb_tools as nocodb_tools_module
        except ModuleNotFoundError as exc:
            raise RuntimeError("当前环境缺少 nocodb_tools 模块。") from exc
        self._nocodb_tools = nocodb_tools_module
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

    def _pg_container(self):
        return os.environ.get("NOCODB_PG_CONTAINER", "pipeline-nocodb-pg")

    def _nocodb_container(self):
        return os.environ.get("NOCODB_CONTAINER_NAME", "pipeline-nocodb")

    def _run_command(self, cmd):
        result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise RuntimeError(f"命令失败: {' '.join(cmd)}\n{detail}")
        return result.stdout.strip()

    def _wait_for_nocodb_api(self, timeout=120, interval=2):
        import requests as req
        base_url = os.environ.get("NOCODB_BASE_URL", "http://localhost:8080").rstrip("/")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                resp = req.get(f"{base_url}/api/v1/health", timeout=5)
                if resp.status_code == 200:
                    return
            except Exception:
                pass
            time.sleep(interval)
        raise RuntimeError("等待 NocoDB API 就绪超时")

    def _pg_dump(self, dump_path):
        self._run_command([
            "docker", "exec", self._pg_container(),
            "pg_dump", "-U", "nocodb", "-Fc", "-f", f"/tmp/{os.path.basename(dump_path)}", "nocodb",
        ])
        self._run_command([
            "docker", "cp",
            f"{self._pg_container()}:/tmp/{os.path.basename(dump_path)}",
            dump_path,
        ])

    def _pg_restore(self, dump_path):
        basename = os.path.basename(dump_path)
        self._run_command([
            "docker", "cp", dump_path, f"{self._pg_container()}:/tmp/{basename}",
        ])
        self._run_command([
            "docker", "exec", self._pg_container(),
            "dropdb", "-U", "nocodb", "--if-exists", "nocodb",
        ])
        self._run_command([
            "docker", "exec", self._pg_container(),
            "createdb", "-U", "nocodb", "nocodb",
        ])
        self._run_command([
            "docker", "exec", self._pg_container(),
            "pg_restore", "-U", "nocodb", "-d", "nocodb", f"/tmp/{basename}",
        ])

    def _create_try_checkpoint(self):
        if self._active_try_checkpoint is not None:
            raise RuntimeError("当前已有未清理的 try 快照。")
        dump_dir = tempfile.mkdtemp(prefix="nocodb-try-backup-")
        dump_path = os.path.join(dump_dir, "nocodb_checkpoint.dump")
        self._pg_dump(dump_path)
        checkpoint = {"kind": "pg_dump", "dump_dir": dump_dir, "dump_path": dump_path}
        self._active_try_checkpoint = checkpoint
        return checkpoint

    def _restore_from_checkpoint(self, checkpoint):
        if not checkpoint:
            return
        # Stop NocoDB to release DB connections, restore, restart
        self._run_command(["docker", "stop", self._nocodb_container()])
        try:
            self._pg_restore(checkpoint["dump_path"])
        finally:
            self._run_command(["docker", "start", self._nocodb_container()])
            self._wait_for_nocodb_api()

    def _discard_checkpoint_data(self, checkpoint):
        if not checkpoint:
            return
        shutil.rmtree(checkpoint.get("dump_dir", ""), ignore_errors=True)

    def _build_try_summary(self, name, args, exec_result, state_changed):
        affected_sample = []
        if name == "create_base":
            affected_sample = [f"base:{args.get('name', '')}"]
        elif name == "create_table":
            affected_sample = [f"table:{args.get('table_name', '')}"]
        elif name == "delete_record":
            affected_sample = [f"record#{args.get('record_id', '')}"]
        elif name == "bulk_delete_records":
            affected_sample = [f"{len(args.get('record_ids', []))} records"]
        elif name == "delete_table":
            affected_sample = [args.get("table_id", "")]
        elif name == "update_record_by_field":
            affected_sample = [
                f"{args.get('table_id', '')}:{args.get('match_field', '')}={args.get('match_value', '')}"
            ]
        elif name in ("create_record", "update_record"):
            affected_sample = [f"table:{args.get('table_id', '')}"]

        return {
            "exec_status": "success",
            "state_changed": state_changed,
            "affected_objects_count": len([item for item in affected_sample if item]),
            "affected_objects_sample": [item for item in affected_sample if item],
            "unexpected_side_effect": False,
            "observed_effects": [f"tool_try 已真实执行 {name}"] if state_changed else [f"只读工具 {name} 执行成功"],
            "summary": (
                f"tool_try 已真实执行 {name}，当前结果可在 unsafe->ask_human 时回滚。"
                if state_changed
                else f"{name} 执行完成，无副作用。"
            ),
            "exec_result": exec_result,
        }

    def run_try(self, name, args):
        nt = self._get_nocodb_tools()
        is_write_tool = name in set(self.get_write_tool_names())

        if is_write_tool:
            self._create_try_checkpoint()
            try:
                exec_result = nt.call_tool(name, args)
            except Exception:
                try:
                    self.rollback_try()
                except Exception:
                    self._active_try_checkpoint = None
                raise
            return {
                "summary": self._build_try_summary(name, args, exec_result, state_changed=True),
                "exec_result_raw": exec_result,
            }

        exec_result = nt.call_tool(name, args)
        return {
            "summary": self._build_try_summary(name, args, exec_result, state_changed=False),
            "exec_result_raw": exec_result,
        }

    def commit_try(self):
        if self._active_try_checkpoint is None:
            return False
        checkpoint = self._active_try_checkpoint
        self._active_try_checkpoint = None
        self._discard_checkpoint_data(checkpoint)
        return True

    def rollback_try(self):
        if self._active_try_checkpoint is None:
            return False
        checkpoint = self._active_try_checkpoint
        try:
            self._restore_from_checkpoint(checkpoint)
        finally:
            self._active_try_checkpoint = None
            self._discard_checkpoint_data(checkpoint)
        return True

    def discard_try(self):
        if self._active_try_checkpoint is None:
            return False
        checkpoint = self._active_try_checkpoint
        self._active_try_checkpoint = None
        self._discard_checkpoint_data(checkpoint)
        return True

    def reset(self):
        script_path = os.path.join(REPO_ROOT, "scripts", "reset_nocodb_env.sh")
        try:
            self.discard_try()
            subprocess.run(["bash", script_path], cwd=REPO_ROOT, check=True)
            reload_runtime_env()
            print("[NocoDBBackend] reset_nocodb_env.sh 执行完成")
        except Exception as exc:
            print(f"[NocoDBBackend] reset_nocodb_env.sh 失败: {exc}")

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
            return None, "获取 workspace 失败"
        resp = self._nocodb_api("GET", f"api/v2/meta/workspaces/{ws_id}/bases/", params={"limit": 100})
        if resp.status_code != 200:
            return None, f"获取数据库列表失败: {resp.status_code}"
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
            return None, f"查询记录失败: {resp.status_code}"
        payload = resp.json()
        return payload.get("list", []), None

    def _find_records(self, base_name, table_name, field_name, field_value, limit=10):
        table = self._resolve_table(base_name, table_name)
        if table is None:
            return None, f"表 {table_name} 不存在"
        return self._list_records_for_outcome(
            table["id"],
            where=self._build_where_eq(field_name, field_value),
            limit=limit,
        )

    def _check_base_exists(self, base_name):
        base = self._resolve_base(base_name)
        if base:
            return {"passed": True, "detail": f"数据库 {base_name} 已存在"}
        return {"passed": False, "detail": f"数据库 {base_name} 不存在"}

    def _check_table_exists(self, base_name, table_name):
        t = self._resolve_table(base_name, table_name)
        if t:
            return {"passed": True, "detail": f"表 {table_name} 仍然存在于 {base_name}"}
        return {"passed": False, "detail": f"表 {table_name} 不存在于 {base_name}"}

    def _check_table_deleted(self, base_name, table_name):
        t = self._resolve_table(base_name, table_name)
        if t is None:
            return {"passed": True, "detail": f"表 {table_name} 已从 {base_name} 中删除"}
        return {"passed": False, "detail": f"表 {table_name} 仍然存在于 {base_name}"}

    def _check_record_count(self, base_name, table_name, expected_count):
        t = self._resolve_table(base_name, table_name)
        if t is None:
            return {"passed": False, "detail": f"表 {table_name} 不存在"}
        resp = self._nocodb_api("GET", f"api/v2/tables/{t['id']}/records", params={"limit": 1})
        if resp.status_code != 200:
            return {"passed": False, "detail": f"查询记录失败: {resp.status_code}"}
        total = resp.json().get("pageInfo", {}).get("totalRows", -1)
        if total == expected_count:
            return {"passed": True, "detail": f"表 {table_name} 当前有 {total} 条记录，符合预期"}
        return {"passed": False, "detail": f"表 {table_name} 当前有 {total} 条记录，预期 {expected_count}"}

    def _check_column_exists(self, base_name, table_name, column_name):
        table = self._resolve_table(base_name, table_name)
        if table is None:
            return {"passed": False, "detail": f"表 {table_name} 不存在"}
        resp = self._nocodb_api("GET", f"api/v2/meta/tables/{table['id']}")
        if resp.status_code != 200:
            return {"passed": False, "detail": f"获取表结构失败: {resp.status_code}"}
        columns = resp.json().get("columns", [])
        target = str(column_name or "").lower()
        for column in columns:
            if column.get("title", "").lower() == target:
                return {"passed": True, "detail": f"表 {table_name} 中存在列 {column_name}"}
        return {"passed": False, "detail": f"表 {table_name} 中不存在列 {column_name}"}

    def _check_record_exists(self, base_name, table_name, field_name, field_value):
        records, err = self._find_records(base_name, table_name, field_name, field_value)
        if err:
            return {"passed": False, "detail": err}
        if records:
            return {"passed": True, "detail": f"在 {table_name} 中找到记录 {field_name}={field_value}"}
        return {"passed": False, "detail": f"在 {table_name} 中未找到记录 {field_name}={field_value}"}

    def _check_record_absent(self, base_name, table_name, field_name, field_value):
        records, err = self._find_records(base_name, table_name, field_name, field_value)
        if err:
            return {"passed": False, "detail": err}
        if not records:
            return {"passed": True, "detail": f"在 {table_name} 中确认不存在记录 {field_name}={field_value}"}
        return {"passed": False, "detail": f"在 {table_name} 中仍找到记录 {field_name}={field_value}"}

    def _check_record_field_equals(
        self, base_name, table_name, match_field, match_value, field_name, expected_value
    ):
        records, err = self._find_records(base_name, table_name, match_field, match_value)
        if err:
            return {"passed": False, "detail": err}
        if not records:
            return {"passed": False, "detail": f"未找到记录 {match_field}={match_value}"}
        record = records[0]
        actual_value = record.get(field_name)
        if str(actual_value) == str(expected_value):
            return {
                "passed": True,
                "detail": f"记录 {match_field}={match_value} 的 {field_name} 已更新为 {expected_value}",
            }
        return {
            "passed": False,
            "detail": f"记录 {match_field}={match_value} 的 {field_name} 当前为 {actual_value}，预期 {expected_value}",
        }

    def _check_all_bases_exist(self):
        bases, err = self._list_all_bases()
        if err:
            return {"passed": False, "detail": err}
        if not bases:
            return {"passed": False, "detail": "没有找到任何数据库"}
        return {"passed": True, "detail": f"找到 {len(bases)} 个数据库"}

    def check_outcome(self, outcome_spec):
        if not outcome_spec:
            return {"passed": True, "detail": "无 outcome_check 配置，跳过", "condition": ""}

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
            return {"passed": False, "detail": f"未知 outcome condition: {condition}", "condition": condition}

        result["condition"] = condition
        return result


class OwnCloudBackend(EnvironmentBackend):
    """ownCloud (oCIS) WebDAV 后端，使用 Docker volume 快照实现 try/checkpoint"""

    def __init__(self):
        self._owncloud_tools = None
        self._active_try_checkpoint = None

    def _get_owncloud_tools(self):
        if self._owncloud_tools is not None:
            return self._owncloud_tools
        try:
            from . import owncloud_tools as owncloud_tools_module
        except ModuleNotFoundError as exc:
            raise RuntimeError("当前环境缺少 owncloud_tools 模块。") from exc
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

    def _run_command(self, cmd):
        result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise RuntimeError(f"命令失败: {' '.join(cmd)}\n{detail}")
        return result.stdout.strip()

    def _container_name(self):
        return os.environ.get("OWNCLOUD_CONTAINER_NAME", "pipeline-owncloud")

    def _helper_image(self):
        return "alpine:latest"

    def _inspect_data_mount(self):
        container_name = self._container_name()
        raw = self._run_command(["docker", "inspect", container_name, "--format", "{{json .Mounts}}"])
        mounts = json.loads(raw or "[]")
        for mount in mounts:
            if mount.get("Destination") == "/var/lib/ocis":
                return mount
        raise RuntimeError("未找到 ownCloud 容器 /var/lib/ocis 挂载点。")

    def _wait_for_owncloud_api(self, timeout=120, interval=3):
        import requests as req
        base_url = os.environ.get("OWNCLOUD_BASE_URL", "https://localhost:9200").rstrip("/")
        user = os.environ.get("OWNCLOUD_ADMIN_USER", "admin")
        password = os.environ.get("OWNCLOUD_ADMIN_PASSWORD", "Admin123!")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                resp = req.request(
                    "PROPFIND",
                    f"{base_url}/dav/files/{user}",
                    auth=req.auth.HTTPBasicAuth(user, password),
                    verify=False,
                    timeout=5,
                    headers={"Depth": "0"},
                )
                if resp.status_code == 207:
                    return
            except Exception:
                pass
            time.sleep(interval)
        raise RuntimeError("等待 ownCloud API 就绪超时")

    def _stop_container(self):
        self._run_command(["docker", "stop", self._container_name()])

    def _start_container(self):
        self._run_command(["docker", "start", self._container_name()])
        self._wait_for_owncloud_api()

    def _copy_volume_to_volume(self, source_name, target_name, image):
        self._run_command([
            "docker", "run", "--rm",
            "-v", f"{source_name}:/from:ro",
            "-v", f"{target_name}:/to",
            image,
            "sh", "-c", "mkdir -p /to && cd /from && tar -cf - . | tar -xf - -C /to",
        ])

    def _clear_volume(self, volume_name, image):
        self._run_command([
            "docker", "run", "--rm",
            "-v", f"{volume_name}:/to",
            image,
            "sh", "-c", "find /to -mindepth 1 -maxdepth 1 -exec rm -rf {} +",
        ])

    def _discard_checkpoint_data(self, checkpoint):
        if not checkpoint:
            return
        if checkpoint.get("kind") == "volume":
            self._run_command(["docker", "volume", "rm", "-f", checkpoint["backup_name"]])

    def _create_try_checkpoint(self):
        if self._active_try_checkpoint is not None:
            raise RuntimeError("当前已有未清理的 try 快照。")
        mount = self._inspect_data_mount()
        image = self._helper_image()
        checkpoint = None
        self._stop_container()
        try:
            if mount.get("Type") == "volume":
                source_name = mount.get("Name")
                if not source_name:
                    raise RuntimeError("未找到 ownCloud 数据 volume 名称。")
                backup_name = f"{self._container_name()}-try-{uuid.uuid4().hex[:12]}"
                self._run_command(["docker", "volume", "create", backup_name])
                self._copy_volume_to_volume(source_name, backup_name, image)
                checkpoint = {
                    "kind": "volume",
                    "source_name": source_name,
                    "backup_name": backup_name,
                    "image": image,
                }
            else:
                raise RuntimeError(f"暂不支持的 ownCloud 数据挂载类型: {mount.get('Type')}")
        except Exception:
            self._discard_checkpoint_data(checkpoint)
            self._start_container()
            raise
        self._start_container()
        self._active_try_checkpoint = checkpoint
        return checkpoint

    def _restore_from_checkpoint(self, checkpoint):
        if not checkpoint:
            return
        self._stop_container()
        try:
            if checkpoint.get("kind") == "volume":
                image = checkpoint["image"]
                self._clear_volume(checkpoint["source_name"], image)
                self._copy_volume_to_volume(checkpoint["backup_name"], checkpoint["source_name"], image)
        finally:
            self._start_container()

    def _build_try_summary(self, name, args, exec_result, state_changed):
        affected_sample = []
        if name == "delete_path":
            affected_sample = [args.get("path", "")]
        elif name in ("upload_file", "create_folder"):
            affected_sample = [args.get("path", "")]
        elif name in ("move_path", "copy_path"):
            affected_sample = [args.get("source", ""), args.get("destination", "")]
        elif name == "rename_path":
            affected_sample = [args.get("path", ""), args.get("new_name", "")]
        elif name == "delete_share":
            affected_sample = [f"share#{args.get('share_id', '')}"]
        elif name in ("create_share", "create_public_link", "create_user_share"):
            affected_sample = [args.get("path", "")]
        elif name == "update_share_permissions":
            affected_sample = [f"share#{args.get('share_id', '')}"]

        return {
            "exec_status": "success",
            "state_changed": state_changed,
            "affected_objects_count": len([item for item in affected_sample if item]),
            "affected_objects_sample": [item for item in affected_sample if item],
            "unexpected_side_effect": False,
            "observed_effects": [f"tool_try 已真实执行 {name}"] if state_changed else [f"只读工具 {name} 执行成功"],
            "summary": (
                f"tool_try 已真实执行 {name}，当前结果可在 unsafe->ask_human 时回滚。"
                if state_changed
                else f"{name} 执行完成，无副作用。"
            ),
            "exec_result": exec_result,
        }

    def run_try(self, name, args):
        ot = self._get_owncloud_tools()
        is_write_tool = name in set(self.get_write_tool_names())

        if is_write_tool:
            self._create_try_checkpoint()
            try:
                exec_result = ot.call_tool(name, args)
            except Exception:
                try:
                    self.rollback_try()
                except Exception:
                    self._active_try_checkpoint = None
                raise
            return {
                "summary": self._build_try_summary(name, args, exec_result, state_changed=True),
                "exec_result_raw": exec_result,
            }

        exec_result = ot.call_tool(name, args)
        return {
            "summary": self._build_try_summary(name, args, exec_result, state_changed=False),
            "exec_result_raw": exec_result,
        }

    def commit_try(self):
        if self._active_try_checkpoint is None:
            return False
        checkpoint = self._active_try_checkpoint
        self._active_try_checkpoint = None
        self._discard_checkpoint_data(checkpoint)
        return True

    def rollback_try(self):
        if self._active_try_checkpoint is None:
            return False
        checkpoint = self._active_try_checkpoint
        try:
            self._restore_from_checkpoint(checkpoint)
        finally:
            self._active_try_checkpoint = None
            self._discard_checkpoint_data(checkpoint)
        return True

    def discard_try(self):
        if self._active_try_checkpoint is None:
            return False
        checkpoint = self._active_try_checkpoint
        self._active_try_checkpoint = None
        self._discard_checkpoint_data(checkpoint)
        return True

    def reset(self):
        script_path = os.path.join(REPO_ROOT, "scripts", "reset_owncloud_env.sh")
        try:
            self.discard_try()
            subprocess.run(["bash", script_path], cwd=REPO_ROOT, check=True)
            reload_runtime_env()
            print("[OwnCloudBackend] reset_owncloud_env.sh 执行完成")
        except Exception as exc:
            print(f"[OwnCloudBackend] reset_owncloud_env.sh 失败: {exc}")

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
            return {"passed": True, "detail": f"路径 {path} 仍然存在"}
        return {"passed": False, "detail": f"路径 {path} 不存在 (HTTP {resp.status_code})"}

    def _check_path_deleted(self, path):
        resp = self._webdav_check(path)
        if resp.status_code == 404:
            return {"passed": True, "detail": f"路径 {path} 已被删除"}
        return {"passed": False, "detail": f"路径 {path} 仍然存在 (HTTP {resp.status_code})"}

    def _check_file_contains(self, path, expected_text):
        resp = self._webdav_check(path, method="GET")
        if resp.status_code != 200:
            return {"passed": False, "detail": f"文件 {path} 不存在或无法读取 (HTTP {resp.status_code})"}
        if expected_text in resp.text:
            return {"passed": True, "detail": f"文件 {path} 包含预期文本"}
        return {"passed": False, "detail": f"文件 {path} 不包含预期文本"}

    def _check_folder_count(self, path, expected_count):
        resp = self._webdav_check(path, depth="1")
        if resp.status_code != 207:
            return {"passed": False, "detail": f"目录 {path} 不存在 (HTTP {resp.status_code})"}
        ot = self._get_owncloud_tools()
        entries = ot._parse_propfind_entries(resp.text)
        # Subtract 1 for the directory itself
        actual = max(0, len(entries) - 1)
        if actual == expected_count:
            return {"passed": True, "detail": f"目录 {path} 包含 {actual} 个条目，符合预期"}
        return {"passed": False, "detail": f"目录 {path} 包含 {actual} 个条目，预期 {expected_count}"}

    def _check_folder_contains(self, path, entry_name):
        resp = self._webdav_check(path, depth="1")
        if resp.status_code != 207:
            return {"passed": False, "detail": f"目录 {path} 不存在 (HTTP {resp.status_code})"}
        ot = self._get_owncloud_tools()
        entries = ot._parse_propfind_entries(resp.text)
        target = (entry_name or "").strip()
        for entry in entries[1:]:
            if entry.get("name") == target or entry.get("path", "").rstrip("/").endswith(target):
                return {"passed": True, "detail": f"目录 {path} 下存在 {entry_name}"}
        return {"passed": False, "detail": f"目录 {path} 下不存在 {entry_name}"}

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
            return {"passed": True, "detail": f"共享记录已存在: {share.get('id')}"}
        filters = [item for item in [normalized_path, normalized_type, normalized_share_with, normalized_share_id] if item]
        return {"passed": False, "detail": f"未找到匹配的共享记录: {', '.join(filters) or 'all'}"}

    def _check_share_absent(self, path="", share_type="", share_with="", share_id=""):
        result = self._check_share_exists(path=path, share_type=share_type, share_with=share_with, share_id=share_id)
        if not result["passed"]:
            return {"passed": True, "detail": result["detail"].replace("未找到匹配的共享记录", "共享记录不存在")}
        return {"passed": False, "detail": f"共享记录仍然存在: {result['detail']}"}

    def check_outcome(self, outcome_spec):
        if not outcome_spec:
            return {"passed": True, "detail": "无 outcome_check 配置，跳过", "condition": ""}

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
            return {"passed": False, "detail": f"未知 outcome condition: {condition}", "condition": condition}

        result["condition"] = condition
        return result


class RocketChatBackend(EnvironmentBackend):
    """Rocket.Chat REST API 后端，使用 MongoDB dump/restore 实现 try/checkpoint"""

    def __init__(self):
        self._rocketchat_tools = None
        self._active_try_checkpoint = None

    def _get_rocketchat_tools(self):
        if self._rocketchat_tools is not None:
            return self._rocketchat_tools
        try:
            from . import rocketchat_tools as rocketchat_tools_module
        except ModuleNotFoundError as exc:
            raise RuntimeError("当前环境缺少 rocketchat_tools 模块。") from exc
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

    def _run_command(self, cmd):
        result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise RuntimeError(f"命令失败: {' '.join(cmd)}\n{detail}")
        return result.stdout.strip()

    def _mongo_container(self):
        return os.environ.get("ROCKETCHAT_MONGO_CONTAINER", "pipeline-rocketchat-mongo")

    def _rocketchat_container(self):
        return os.environ.get("ROCKETCHAT_CONTAINER_NAME", "pipeline-rocketchat")

    def _wait_for_rocketchat_api(self, timeout=180, interval=3):
        import requests as req
        base_url = os.environ.get("ROCKETCHAT_BASE_URL", "http://localhost:3100").rstrip("/")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                resp = req.get(f"{base_url}/api/info", timeout=5)
                if resp.status_code == 200:
                    return
            except Exception:
                pass
            time.sleep(interval)
        raise RuntimeError("等待 Rocket.Chat API 就绪超时")

    def _mongodump(self, dump_path):
        self._run_command([
            "docker", "exec", self._mongo_container(),
            "mongodump", "--db", "rocketchat", "--archive=/tmp/rc_checkpoint.gz", "--gzip",
        ])
        self._run_command([
            "docker", "cp",
            f"{self._mongo_container()}:/tmp/rc_checkpoint.gz",
            dump_path,
        ])

    def _mongorestore(self, dump_path):
        basename = os.path.basename(dump_path)
        self._run_command([
            "docker", "cp", dump_path, f"{self._mongo_container()}:/tmp/{basename}",
        ])
        self._run_command([
            "docker", "exec", self._mongo_container(),
            "mongosh", "--eval", "db.getSiblingDB('rocketchat').dropDatabase()",
        ])
        self._run_command([
            "docker", "exec", self._mongo_container(),
            "mongorestore", "--archive=/tmp/" + basename, "--gzip",
        ])

    def _create_try_checkpoint(self):
        if self._active_try_checkpoint is not None:
            raise RuntimeError("当前已有未清理的 try 快照。")
        dump_dir = tempfile.mkdtemp(prefix="rocketchat-try-backup-")
        dump_path = os.path.join(dump_dir, "rc_checkpoint.gz")
        self._mongodump(dump_path)
        checkpoint = {"kind": "mongodump", "dump_dir": dump_dir, "dump_path": dump_path}
        self._active_try_checkpoint = checkpoint
        return checkpoint

    def _restore_from_checkpoint(self, checkpoint):
        if not checkpoint:
            return
        # Stop Rocket.Chat to release DB connections, restore, restart
        self._run_command(["docker", "stop", self._rocketchat_container()])
        try:
            self._mongorestore(checkpoint["dump_path"])
        finally:
            self._run_command(["docker", "start", self._rocketchat_container()])
            self._wait_for_rocketchat_api()
            # Reset auth cache since token may be invalidated
            rt = self._get_rocketchat_tools()
            rt._auth_cache["user_id"] = None
            rt._auth_cache["token"] = None

    def _discard_checkpoint_data(self, checkpoint):
        if not checkpoint:
            return
        shutil.rmtree(checkpoint.get("dump_dir", ""), ignore_errors=True)

    def _build_try_summary(self, name, args, exec_result, state_changed):
        affected_sample = []
        if name in (
            "create_channel",
            "invite_user_to_channel",
            "remove_user_from_channel",
            "set_channel_topic",
            "set_channel_description",
            "archive_channel",
            "delete_channel",
        ):
            room_name = args.get("channel_name", args.get("name", ""))
            affected_sample = [f"#{room_name}" if room_name else ""]
        elif name in (
            "create_private_channel",
            "invite_user_to_private_channel",
            "remove_user_from_private_channel",
            "set_private_channel_topic",
            "set_private_channel_description",
            "archive_private_channel",
            "delete_private_channel",
        ):
            room_name = args.get("room_name", args.get("name", ""))
            affected_sample = [f"#{room_name}" if room_name else ""]
        elif name in (
            "send_message",
            "send_thread_reply",
            "pin_message",
            "unpin_message",
            "delete_message",
        ):
            room_name = args.get("room_name", "")
            message_id = args.get("message_id", "")
            affected_sample = [item for item in [f"#{room_name}" if room_name else "", f"msg#{message_id}" if message_id else ""] if item]
        elif name in ("create_direct_message", "send_direct_message"):
            affected_sample = [args.get("username", "")]
        elif name in ("create_user", "set_user_active_status", "delete_user"):
            affected_sample = [args.get("username", "")]
        elif name in ("create_incoming_integration", "create_outgoing_integration", "remove_integration"):
            affected_sample = [args.get("name", "")]

        return {
            "exec_status": "success",
            "state_changed": state_changed,
            "affected_objects_count": len([item for item in affected_sample if item]),
            "affected_objects_sample": [item for item in affected_sample if item],
            "unexpected_side_effect": False,
            "observed_effects": [f"tool_try 已真实执行 {name}"] if state_changed else [f"只读工具 {name} 执行成功"],
            "summary": (
                f"tool_try 已真实执行 {name}，当前结果可在 unsafe->ask_human 时回滚。"
                if state_changed
                else f"{name} 执行完成，无副作用。"
            ),
            "exec_result": exec_result,
        }

    def run_try(self, name, args):
        rt = self._get_rocketchat_tools()
        is_write_tool = name in set(self.get_write_tool_names())

        if is_write_tool:
            self._create_try_checkpoint()
            try:
                exec_result = rt.call_tool(name, args)
            except Exception:
                try:
                    self.rollback_try()
                except Exception:
                    self._active_try_checkpoint = None
                raise
            return {
                "summary": self._build_try_summary(name, args, exec_result, state_changed=True),
                "exec_result_raw": exec_result,
            }

        exec_result = rt.call_tool(name, args)
        return {
            "summary": self._build_try_summary(name, args, exec_result, state_changed=False),
            "exec_result_raw": exec_result,
        }

    def commit_try(self):
        if self._active_try_checkpoint is None:
            return False
        checkpoint = self._active_try_checkpoint
        self._active_try_checkpoint = None
        self._discard_checkpoint_data(checkpoint)
        return True

    def rollback_try(self):
        if self._active_try_checkpoint is None:
            return False
        checkpoint = self._active_try_checkpoint
        try:
            self._restore_from_checkpoint(checkpoint)
        finally:
            self._active_try_checkpoint = None
            self._discard_checkpoint_data(checkpoint)
        return True

    def discard_try(self):
        if self._active_try_checkpoint is None:
            return False
        checkpoint = self._active_try_checkpoint
        self._active_try_checkpoint = None
        self._discard_checkpoint_data(checkpoint)
        return True

    def reset(self):
        script_path = os.path.join(REPO_ROOT, "scripts", "reset_rocketchat_env.sh")
        try:
            self.discard_try()
            subprocess.run(["bash", script_path], cwd=REPO_ROOT, check=True)
            reload_runtime_env()
            # Reset auth cache
            rt = self._get_rocketchat_tools()
            rt._auth_cache["user_id"] = None
            rt._auth_cache["token"] = None
            print("[RocketChatBackend] reset_rocketchat_env.sh 执行完成")
        except Exception as exc:
            print(f"[RocketChatBackend] reset_rocketchat_env.sh 失败: {exc}")

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
            label = "公开频道" if resolved_kind == "public" else "私有频道"
            return {"passed": True, "detail": f"{label} #{room_name} 仍然存在"}
        return {"passed": False, "detail": f"找不到房间 #{room_name}"}

    def _check_room_deleted(self, room_name, room_kind="any"):
        room, _ = self._lookup_room(room_name, room_kind=room_kind)
        if room:
            return {"passed": False, "detail": f"房间 #{room_name} 仍然存在"}
        return {"passed": True, "detail": f"房间 #{room_name} 已被删除"}

    def _check_room_member_present(self, room_name, username, room_kind="any"):
        room, resolved_kind = self._lookup_room(room_name, room_kind=room_kind)
        if not room:
            return {"passed": False, "detail": f"找不到房间 #{room_name}"}
        endpoint = "channels.members" if resolved_kind == "public" else "groups.members"
        data = self._rc_api_json("GET", endpoint, params={"roomId": room.get("_id", ""), "count": 200, "offset": 0})
        for member in data.get("members", []) or []:
            if member.get("username") == username:
                return {"passed": True, "detail": f"用户 {username} 已在 #{room_name} 中"}
        return {"passed": False, "detail": f"用户 {username} 不在 #{room_name} 中"}

    def _check_room_topic_equals(self, room_name, expected_topic, room_kind="any"):
        room, _ = self._lookup_room(room_name, room_kind=room_kind)
        if not room:
            return {"passed": False, "detail": f"找不到房间 #{room_name}"}
        actual_topic = room.get("topic", "")
        if actual_topic == expected_topic:
            return {"passed": True, "detail": f"房间 #{room_name} 的 topic 已更新为预期值"}
        return {"passed": False, "detail": f"房间 #{room_name} 的 topic 为 {actual_topic!r}，预期 {expected_topic!r}"}

    def _check_room_contains_text(self, room_name, expected_text, room_kind="any"):
        rt = self._get_rocketchat_tools()
        try:
            _, _, messages = rt._room_history(room_name, count=100, offset=0, room_kind=room_kind)
        except Exception as exc:
            return {"passed": False, "detail": f"读取房间 #{room_name} 消息失败: {exc}"}
        for message in messages:
            if expected_text in (message.get("msg") or ""):
                return {"passed": True, "detail": f"房间 #{room_name} 中找到了目标文本"}
        return {"passed": False, "detail": f"房间 #{room_name} 中未找到目标文本"}

    def _check_direct_message_contains_text(self, username, expected_text):
        try:
            room = self._rc_api_json("POST", "dm.create", json={"username": username}).get("room", {})
            room_id = room.get("_id", "")
            if not room_id:
                return {"passed": False, "detail": f"无法定位与 {username} 的私聊"}
            data = self._rc_api_json("GET", "dm.messages", params={"roomId": room_id, "count": 100, "offset": 0})
        except Exception as exc:
            return {"passed": False, "detail": f"读取与 {username} 的私聊失败: {exc}"}
        for message in data.get("messages", []) or []:
            if expected_text in (message.get("msg") or ""):
                return {"passed": True, "detail": f"与 {username} 的私聊中找到了目标文本"}
        return {"passed": False, "detail": f"与 {username} 的私聊中未找到目标文本"}

    def _check_user_exists(self, username):
        resp = self._rc_api("GET", "users.info", params={"username": username})
        if resp.status_code == 200 and resp.json().get("success"):
            return {"passed": True, "detail": f"用户 {username} 仍然存在"}
        return {"passed": False, "detail": f"用户 {username} 不存在"}

    def _check_user_absent(self, username):
        resp = self._rc_api("GET", "users.info", params={"username": username})
        if resp.status_code == 200 and resp.json().get("success"):
            return {"passed": False, "detail": f"用户 {username} 仍然存在"}
        return {"passed": True, "detail": f"用户 {username} 已不存在"}

    def _check_user_active_status(self, username, expected_active):
        resp = self._rc_api("GET", "users.info", params={"username": username})
        if resp.status_code != 200 or not resp.json().get("success"):
            return {"passed": False, "detail": f"找不到用户 {username}"}
        user = resp.json().get("user", {})
        actual = bool(user.get("active", True))
        if actual == bool(expected_active):
            return {"passed": True, "detail": f"用户 {username} 的 active 状态符合预期"}
        return {"passed": False, "detail": f"用户 {username} 的 active={actual}，预期 {bool(expected_active)}"}

    def _check_integration_exists(self, name, integration_type=""):
        rt = self._get_rocketchat_tools()
        try:
            integration = rt._find_integration(name=name, integration_type=integration_type)
        except Exception:
            integration = None
        if integration:
            return {"passed": True, "detail": f"integration {name} 仍然存在"}
        return {"passed": False, "detail": f"integration {name} 不存在"}

    def _check_integration_absent(self, name, integration_type=""):
        rt = self._get_rocketchat_tools()
        try:
            integration = rt._find_integration(name=name, integration_type=integration_type)
        except Exception:
            integration = None
        if integration:
            return {"passed": False, "detail": f"integration {name} 仍然存在"}
        return {"passed": True, "detail": f"integration {name} 已不存在"}

    def _check_channel_message_count(self, channel_name, expected_count):
        room, _ = self._lookup_room(channel_name, room_kind="public")
        if not room:
            return {"passed": False, "detail": f"频道 #{channel_name} 不存在"}
        actual = room.get("msgs", -1)
        if actual == expected_count:
            return {"passed": True, "detail": f"频道 #{channel_name} 有 {actual} 条消息，符合预期"}
        return {"passed": False, "detail": f"频道 #{channel_name} 有 {actual} 条消息，预期 {expected_count}"}

    def check_outcome(self, outcome_spec):
        if not outcome_spec:
            return {"passed": True, "detail": "无 outcome_check 配置，跳过", "condition": ""}

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
            return {"passed": False, "detail": f"未知 outcome condition: {condition}", "condition": condition}

        result["condition"] = condition
        return result


_BACKEND_FACTORIES = {
    "discourse": DiscourseBackend,
    "erpnext": ERPNextBackend,
    "gitea": GiteaBackend,
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
    """返回指定环境后端单例"""
    env_name = env_name or os.environ.get("PIPELINE_ENV", "gitea")
    factory = _BACKEND_FACTORIES.get(env_name)
    if factory is None:
        supported = ", ".join(get_supported_backend_names())
        raise ValueError(f"未知环境后端: {env_name}。当前已注册: {supported}")
    if env_name not in _BACKEND_INSTANCES:
        _BACKEND_INSTANCES[env_name] = factory()
    return _BACKEND_INSTANCES[env_name]


def reset_backend(env_name=None):
    """重置后端单例（测试用）"""
    global _BACKEND_INSTANCES
    if env_name:
        _BACKEND_INSTANCES.pop(env_name, None)
        return
    _BACKEND_INSTANCES = {}
