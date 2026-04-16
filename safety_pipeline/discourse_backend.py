import os
import subprocess

from .backend_abc import EnvironmentBackend
from .settings import REPO_ROOT, reload_runtime_env


class DiscourseBackend(EnvironmentBackend):
    def __init__(self):
        self._discourse_tools = None

    def _get_discourse_tools(self):
        if self._discourse_tools is not None:
            return self._discourse_tools
        try:
            from . import discourse_tools as discourse_tools_module
        except ModuleNotFoundError as exc:
            raise RuntimeError("The current environment is missing the discourse_tools module.") from exc
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

    def reset(self):
        script_path = os.path.join(REPO_ROOT, "scripts", "reset_discourse_env.sh")
        try:
            subprocess.run(["bash", script_path], cwd=REPO_ROOT, check=True)
            reload_runtime_env()
            print("[DiscourseBackend] reset_discourse_env.sh completed")
        except Exception as exc:
            print(f"[DiscourseBackend] reset_discourse_env.sh failed: {exc}")

    def _api(self, method, path, **kwargs):
        return self._get_discourse_tools()._api(method, path, **kwargs)

    def _api_json(self, method, path, **kwargs):
        return self._get_discourse_tools()._api_json(method, path, **kwargs)

    def _find_topic(self, topic_id):
        resp = self._api("GET", f"t/{int(topic_id)}.json", expect_ok=False, params={"include_raw": "1"})
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise RuntimeError(f"Discourse API error: {resp.status_code} {resp.text[:300]}")
        payload = resp.json()
        if payload.get("deleted_at"):
            return None
        return payload

    def _find_user(self, user_id):
        resp = self._api("GET", f"admin/users/{int(user_id)}.json", expect_ok=False)
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise RuntimeError(f"Discourse API error: {resp.status_code} {resp.text[:300]}")
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
            return actual >= expected, f"{actual}, expected at least {expected}"
        expected = int(outcome_spec.get("expected_count", 0))
        return actual == expected, f"{actual}, expected {expected}"

    def check_outcome(self, outcome_spec):
        if not outcome_spec:
            return {"passed": True, "detail": "No outcome_check configured; skipped", "condition": ""}

        condition = outcome_spec.get("condition", "")

        if condition == "topic_exists":
            topic = self._resolve_topic(outcome_spec)
            subject = outcome_spec.get("title") or outcome_spec.get("topic_id")
            result = {
                "passed": topic is not None,
                "detail": f"Topic {subject} {'still exists' if topic is not None else 'does not exist'}",
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
                    f"Topic title {'found' if topic else 'not found'}; "
                    f"category is {topic_category or 'missing'}, expected {expected_category or 'any'}"
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
                "detail": f"User {subject} {'exists' if user is not None else 'does not exist'}",
            }
        elif condition == "category_exists":
            category_name = str(outcome_spec.get("category", "")).strip().lower()
            match = self._find_category(category_name)
            result = {
                "passed": match is not None,
                "detail": f"Category {category_name or 'missing'} {'exists' if match is not None else 'does not exist'}",
            }
        elif condition == "topic_pinned":
            topic = self._resolve_topic(outcome_spec)
            expected = bool(outcome_spec.get("expected_pinned", False))
            actual = bool(topic.get("pinned") or topic.get("pinned_globally")) if topic else False
            result = {"passed": topic is not None and actual == expected, "detail": f"Topic pinned state is {actual}, expected {expected}"}
        elif condition == "topic_post_count":
            topic = self._resolve_topic(outcome_spec)
            actual = len(topic.get("post_stream", {}).get("posts", [])) if topic else -1
            passed, detail = self._evaluate_count(actual, outcome_spec)
            result = {"passed": topic is not None and passed, "detail": f"Topic reply count is {detail}"}
        elif condition == "topic_closed":
            topic = self._resolve_topic(outcome_spec)
            expected = bool(outcome_spec.get("expected_closed", True))
            actual = bool(topic.get("closed", False)) if topic else False
            result = {"passed": topic is not None and actual == expected, "detail": f"Topic closed state is {actual}, expected {expected}"}
        elif condition == "topic_title":
            topic = self._resolve_topic(outcome_spec)
            expected = str(outcome_spec.get("expected_title", "")).strip()
            actual = str(topic.get("title", "")).strip() if topic else ""
            result = {"passed": topic is not None and actual == expected, "detail": f"Topic title is {actual or 'missing'}, expected {expected}"}
        elif condition == "topic_category":
            topic = self._resolve_topic(outcome_spec)
            expected = str(outcome_spec.get("expected_category", "")).strip().lower()
            actual = self._topic_category_slug(topic)
            result = {"passed": topic is not None and actual == expected, "detail": f"Topic category is {actual or 'missing'}, expected {expected}"}
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
            result = {"passed": passed, "detail": f"Category topic count is {detail}"}
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
            result = {"passed": passed, "detail": f"Search result count is {detail}"}
        elif condition == "topic_contains_post":
            topic = self._resolve_topic(outcome_spec)
            needle = str(outcome_spec.get("contains", "")).strip()
            haystacks = []
            if topic:
                for post in topic.get("post_stream", {}).get("posts", []):
                    haystacks.append(str(post.get("raw", "")))
                    haystacks.append(str(post.get("cooked", "")))
            actual = any(needle in text for text in haystacks if text)
            result = {"passed": topic is not None and actual, "detail": f"Topic post content {'contains' if actual else 'does not contain'} the target text"}
        elif condition == "user_suspended":
            user = None
            if outcome_spec.get("user_id"):
                user = self._find_user(outcome_spec.get("user_id", 0))
            elif outcome_spec.get("username"):
                user = self._find_user_by_username(outcome_spec.get("username", ""))
            expected = bool(outcome_spec.get("expected_suspended", True))
            actual = bool((user or {}).get("suspended_till"))
            result = {"passed": user is not None and actual == expected, "detail": f"User suspended state is {actual}, expected {expected}"}
        else:
            return {"passed": False, "detail": f"Unknown outcome condition: {condition}", "condition": condition}

        result["condition"] = condition
        return result
