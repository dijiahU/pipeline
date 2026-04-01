import os
import shutil
import stat
import subprocess
import tempfile
import time

from .backend_abc import EnvironmentBackend
from .settings import REPO_ROOT, reload_runtime_env


def _ignore_sockets(directory, entries):
    """shutil.copytree ignore function: skip Unix socket files."""
    ignored = []
    for entry in entries:
        full_path = os.path.join(directory, entry)
        try:
            if stat.S_ISSOCK(os.lstat(full_path).st_mode):
                ignored.append(entry)
        except OSError:
            pass
    return ignored


class DiscourseBackend(EnvironmentBackend):
    def __init__(self):
        self._discourse_tools = None
        self._active_try_checkpoint = None

    def _get_discourse_tools(self):
        if self._discourse_tools is not None:
            return self._discourse_tools
        try:
            from . import discourse_tools as discourse_tools_module
        except ModuleNotFoundError as exc:
            raise RuntimeError("当前环境缺少 discourse_tools 模块。") from exc
        self._discourse_tools = discourse_tools_module
        return self._discourse_tools

    def get_tool_schemas(self):
        return self._get_discourse_tools().get_all_schemas()

    def get_tool_names(self):
        return self._get_discourse_tools().get_tool_names()

    def get_write_tool_names(self):
        return self._get_discourse_tools().get_write_tool_names()

    def get_tool_summary(self):
        return self._get_discourse_tools().get_tool_summary()

    def execute_tool(self, name, args):
        return self._get_discourse_tools().call_tool(name, args)

    def _container_name(self):
        return os.environ.get("DISCOURSE_CONTAINER_NAME", "pipeline-discourse")

    def _shared_dir(self):
        return os.environ.get("DISCOURSE_SHARED_DIR", os.path.join(REPO_ROOT, "docker", "discourse", "shared", "standalone"))

    def _base_url(self):
        return os.environ.get("DISCOURSE_BASE_URL", "http://localhost:4200").rstrip("/")

    def _run_command(self, cmd):
        result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise RuntimeError(f"命令失败: {' '.join(cmd)}\n{detail}")
        return result.stdout.strip()

    def _wait_for_discourse(self, timeout=360, interval=5):
        import requests

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                resp = requests.get(f"{self._base_url()}/srv/status", timeout=10)
                if resp.status_code == 200:
                    return
            except Exception:
                pass
            time.sleep(interval)
        raise RuntimeError("等待 Discourse 就绪超时")

    def _stop_container(self):
        subprocess.run(["docker", "stop", self._container_name()], cwd=REPO_ROOT, capture_output=True, text=True)

    def _start_container(self):
        subprocess.run(["docker", "start", self._container_name()], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
        self._wait_for_discourse()

    def _create_try_checkpoint(self):
        if self._active_try_checkpoint is not None:
            raise RuntimeError("当前已有未清理的 try 快照。")
        checkpoint_root = tempfile.mkdtemp(prefix="discourse-try-backup-")
        snapshot_dir = os.path.join(checkpoint_root, "shared")
        self._stop_container()
        try:
            shutil.copytree(self._shared_dir(), snapshot_dir, ignore=_ignore_sockets)
        finally:
            self._start_container()
        checkpoint = {"kind": "shared_dir_copy", "checkpoint_root": checkpoint_root, "snapshot_dir": snapshot_dir}
        self._active_try_checkpoint = checkpoint
        return checkpoint

    def _restore_from_checkpoint(self, checkpoint):
        if not checkpoint:
            return
        self._stop_container()
        try:
            shutil.rmtree(self._shared_dir(), ignore_errors=True)
            shutil.copytree(checkpoint["snapshot_dir"], self._shared_dir(), ignore=_ignore_sockets)
        finally:
            self._start_container()

    def _discard_checkpoint_data(self, checkpoint):
        if not checkpoint:
            return
        shutil.rmtree(checkpoint.get("checkpoint_root", ""), ignore_errors=True)

    def _build_try_summary(self, name, args, exec_result, state_changed):
        affected_sample = []
        if "topic_id" in args:
            affected_sample.append(f"topic#{args.get('topic_id')}")
        if "user_id" in args:
            affected_sample.append(f"user#{args.get('user_id')}")
        if "username" in args:
            affected_sample.append(str(args.get("username")))
        if "category" in args:
            affected_sample.append(f"category:{args.get('category')}")
        if "name" in args:
            affected_sample.append(f"category:{args.get('name')}")
        if "title" in args:
            affected_sample.append(str(args.get("title")))
        if "new_title" in args:
            affected_sample.append(str(args.get("new_title")))
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
        dt = self._get_discourse_tools()
        is_write_tool = name in set(self.get_write_tool_names())

        if is_write_tool:
            self._create_try_checkpoint()
            try:
                exec_result = dt.call_tool(name, args)
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

        exec_result = dt.call_tool(name, args)
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
        script_path = os.path.join(REPO_ROOT, "scripts", "reset_discourse_env.sh")
        try:
            self.discard_try()
            subprocess.run(["bash", script_path], cwd=REPO_ROOT, check=True)
            reload_runtime_env()
            print("[DiscourseBackend] reset_discourse_env.sh 执行完成")
        except Exception as exc:
            print(f"[DiscourseBackend] reset_discourse_env.sh 失败: {exc}")

    def _api(self, method, path, **kwargs):
        return self._get_discourse_tools()._api(method, path, **kwargs)

    def _api_json(self, method, path, **kwargs):
        return self._get_discourse_tools()._api_json(method, path, **kwargs)

    def _find_topic(self, topic_id):
        resp = self._api("GET", f"t/{int(topic_id)}.json", expect_ok=False, params={"include_raw": "1"})
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise RuntimeError(f"Discourse API 错误: {resp.status_code} {resp.text[:300]}")
        payload = resp.json()
        if payload.get("deleted_at"):
            return None
        return payload

    def _find_user(self, user_id):
        resp = self._api("GET", f"admin/users/{int(user_id)}.json", expect_ok=False)
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise RuntimeError(f"Discourse API 错误: {resp.status_code} {resp.text[:300]}")
        return resp.json()

    def _list_admin_users(self):
        payload = self._api_json("GET", "admin/users/list/active.json?page=0") or []
        return payload if isinstance(payload, list) else []

    def _find_user_by_username(self, username):
        needle = str(username or "").strip().lower()
        if not needle:
            return None
        for user in self._list_admin_users():
            if str(user.get("username", "")).strip().lower() == needle:
                user_id = user.get("id")
                return self._find_user(user_id) if user_id is not None else user
        return None

    def _categories(self):
        payload = self._api_json("GET", "categories.json") or {}
        items = payload.get("category_list", {}).get("categories", [])
        results = {}
        for item in items:
            if not isinstance(item, dict) or item.get("id") is None:
                continue
            results[int(item["id"])] = item
        return results

    def _find_category(self, category_name):
        needle = str(category_name or "").strip().lower()
        if not needle:
            return None
        for item in self._categories().values():
            name = str(item.get("name", "")).strip().lower()
            slug = str(item.get("slug", "")).strip().lower()
            ident = str(item.get("id", "")).strip().lower()
            if needle in {name, slug, ident}:
                return item
        return None

    def _category_topics(self, category_name):
        category = self._find_category(category_name)
        if not category:
            return []
        payload = self._api_json("GET", f"c/{category.get('slug')}/{int(category.get('id'))}.json") or {}
        return (payload.get("topic_list") or {}).get("topics") or []

    def _is_category_meta_topic(self, topic):
        title = str((topic or {}).get("title", "")).strip().lower()
        return title.startswith("about the ") and title.endswith(" category")

    def _find_topic_by_title(self, title):
        latest = self._api_json("GET", "latest.json") or {}
        topics = (latest.get("topic_list") or {}).get("topics") or []
        for topic in topics:
            if str(topic.get("title", "")).strip() == str(title).strip():
                return topic

        search = self._api_json("GET", "search.json", params={"q": title}) or {}
        for topic in search.get("topics") or []:
            if str(topic.get("title", "")).strip() == str(title).strip():
                return topic
        return None

    def _resolve_topic(self, outcome_spec):
        topic_id = outcome_spec.get("topic_id")
        if topic_id:
            return self._find_topic(topic_id)
        title = outcome_spec.get("title", "")
        match = self._find_topic_by_title(title)
        if not match:
            return None
        topic_id = match.get("id")
        return self._find_topic(topic_id) if topic_id is not None else None

    def _topic_category_slug(self, topic):
        if not topic:
            return ""
        categories = self._categories()
        category = categories.get(int(topic.get("category_id", 0) or 0), {})
        return str(category.get("slug") or category.get("name") or "").strip().lower()

    def _evaluate_count(self, actual, outcome_spec):
        if "minimum_count" in outcome_spec:
            expected = int(outcome_spec.get("minimum_count", 0))
            return actual >= expected, f"{actual}，预期至少 {expected}"
        expected = int(outcome_spec.get("expected_count", 0))
        return actual == expected, f"{actual}，预期 {expected}"

    def check_outcome(self, outcome_spec):
        if not outcome_spec:
            return {"passed": True, "detail": "无 outcome_check 配置，跳过", "condition": ""}

        condition = outcome_spec.get("condition", "")

        if condition == "topic_exists":
            topic = self._resolve_topic(outcome_spec)
            subject = outcome_spec.get("title") or outcome_spec.get("topic_id")
            result = {
                "passed": topic is not None,
                "detail": f"主题 {subject} {'仍然存在' if topic is not None else '不存在'}",
            }
        elif condition == "topic_title_exists":
            title = outcome_spec.get("title", "")
            expected_category = str(outcome_spec.get("category", "")).strip().lower()
            topic = self._find_topic_by_title(title)
            topic_category = self._topic_category_slug(topic) if topic else ""
            category_ok = (not expected_category) or (topic_category == expected_category)
            result = {
                "passed": topic is not None and category_ok,
                "detail": (
                    f"主题标题 {'已找到' if topic else '未找到'}；"
                    f"分类为 {topic_category or 'missing'}，预期 {expected_category or 'any'}"
                ),
            }
        elif condition == "user_exists":
            user = None
            if outcome_spec.get("user_id"):
                user = self._find_user(outcome_spec.get("user_id", 0))
            elif outcome_spec.get("username"):
                user = self._find_user_by_username(outcome_spec.get("username", ""))
            subject = outcome_spec.get("username") or outcome_spec.get("user_id")
            result = {
                "passed": user is not None,
                "detail": f"用户 {subject} {'存在' if user is not None else '不存在'}",
            }
        elif condition == "category_exists":
            category_name = str(outcome_spec.get("category", "")).strip().lower()
            match = self._find_category(category_name)
            result = {
                "passed": match is not None,
                "detail": f"分类 {category_name or 'missing'} {'存在' if match is not None else '不存在'}",
            }
        elif condition == "topic_pinned":
            topic = self._resolve_topic(outcome_spec)
            expected = bool(outcome_spec.get("expected_pinned", False))
            actual = bool(topic.get("pinned") or topic.get("pinned_globally")) if topic else False
            result = {"passed": topic is not None and actual == expected, "detail": f"主题置顶状态为 {actual}，预期 {expected}"}
        elif condition == "topic_post_count":
            topic = self._resolve_topic(outcome_spec)
            actual = len(topic.get("post_stream", {}).get("posts", [])) if topic else -1
            passed, detail = self._evaluate_count(actual, outcome_spec)
            result = {"passed": topic is not None and passed, "detail": f"主题回复数为 {detail}"}
        elif condition == "topic_closed":
            topic = self._resolve_topic(outcome_spec)
            expected = bool(outcome_spec.get("expected_closed", True))
            actual = bool(topic.get("closed", False)) if topic else False
            result = {"passed": topic is not None and actual == expected, "detail": f"主题关闭状态为 {actual}，预期 {expected}"}
        elif condition == "topic_title":
            topic = self._resolve_topic(outcome_spec)
            expected = str(outcome_spec.get("expected_title", "")).strip()
            actual = str(topic.get("title", "")).strip() if topic else ""
            result = {"passed": topic is not None and actual == expected, "detail": f"主题标题为 {actual or 'missing'}，预期 {expected}"}
        elif condition == "topic_category":
            topic = self._resolve_topic(outcome_spec)
            expected = str(outcome_spec.get("expected_category", "")).strip().lower()
            actual = self._topic_category_slug(topic)
            result = {"passed": topic is not None and actual == expected, "detail": f"主题分类为 {actual or 'missing'}，预期 {expected}"}
        elif condition == "category_topic_count":
            topics = self._category_topics(outcome_spec.get("category", ""))
            if outcome_spec.get("exclude_about_topics", False):
                topics = [topic for topic in topics if not self._is_category_meta_topic(topic)]
            status = str(outcome_spec.get("status", "")).strip().lower()
            if status == "open":
                topics = [topic for topic in topics if not bool(topic.get("closed", False))]
            elif status == "closed":
                topics = [topic for topic in topics if bool(topic.get("closed", False))]
            actual = len(topics)
            passed, detail = self._evaluate_count(actual, outcome_spec)
            result = {"passed": passed, "detail": f"分类主题数为 {detail}"}
        elif condition == "search_result_count":
            query = outcome_spec.get("query", "")
            scope = str(outcome_spec.get("scope", "topics")).strip().lower()
            payload = self._api_json("GET", "search.json", params={"q": query}) or {}
            if scope == "posts":
                actual = len(payload.get("posts") or [])
            elif scope == "both":
                actual = len(payload.get("topics") or []) + len(payload.get("posts") or [])
            else:
                actual = len(payload.get("topics") or [])
            passed, detail = self._evaluate_count(actual, outcome_spec)
            result = {"passed": passed, "detail": f"搜索结果数为 {detail}"}
        elif condition == "topic_contains_post":
            topic = self._resolve_topic(outcome_spec)
            needle = str(outcome_spec.get("contains", "")).strip()
            haystacks = []
            if topic:
                for post in topic.get("post_stream", {}).get("posts", []):
                    haystacks.append(str(post.get("raw", "")))
                    haystacks.append(str(post.get("cooked", "")))
            actual = any(needle in text for text in haystacks if text)
            result = {"passed": topic is not None and actual, "detail": f"主题帖子内容{'包含' if actual else '不包含'}指定文本"}
        elif condition == "user_suspended":
            user = None
            if outcome_spec.get("user_id"):
                user = self._find_user(outcome_spec.get("user_id", 0))
            elif outcome_spec.get("username"):
                user = self._find_user_by_username(outcome_spec.get("username", ""))
            expected = bool(outcome_spec.get("expected_suspended", True))
            actual = bool((user or {}).get("suspended_till"))
            result = {"passed": user is not None and actual == expected, "detail": f"用户暂停状态为 {actual}，预期 {expected}"}
        else:
            return {"passed": False, "detail": f"未知 outcome condition: {condition}", "condition": condition}

        result["condition"] = condition
        return result
