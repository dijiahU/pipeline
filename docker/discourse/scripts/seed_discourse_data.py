import json
import os
import sys
import time
from typing import Dict, List, Optional, Set

import requests


BASE_URL = os.environ.get("DISCOURSE_BASE_URL", "http://localhost:4200").rstrip("/")
API_KEY = os.environ.get("DISCOURSE_API_KEY", "")
API_USERNAME = os.environ.get("DISCOURSE_API_USERNAME", "admin")
MANIFEST_PATH = os.environ.get("DISCOURSE_SEED_MANIFEST")


def api(method: str, path: str, *, username: str = "", **kwargs):
    headers = kwargs.pop("headers", {})
    headers.setdefault("Api-Key", API_KEY)
    headers.setdefault("Api-Username", username or API_USERNAME)
    headers.setdefault("Accept", "application/json")
    resp = requests.request(method, f"{BASE_URL}/{path.lstrip('/')}", headers=headers, timeout=30, **kwargs)
    if resp.status_code >= 400:
        raise RuntimeError(f"{method} {path} failed: HTTP {resp.status_code} {resp.text[:500]}")
    return resp


def api_json(method: str, path: str, **kwargs):
    resp = api(method, path, **kwargs)
    if not resp.text:
        return None
    return resp.json()


def load_manifest() -> Dict:
    if not MANIFEST_PATH:
        raise RuntimeError("DISCOURSE_SEED_MANIFEST is not set")
    with open(MANIFEST_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def categories_by_slug() -> Dict[str, Dict]:
    payload = api_json("GET", "categories.json") or {}
    categories = ((payload.get("category_list") or {}).get("categories") or []) if isinstance(payload, dict) else []
    return {item.get("slug", ""): item for item in categories if isinstance(item, dict)}


def ensure_category(name: str) -> Dict:
    slug = name.lower()
    categories = categories_by_slug()
    if slug in categories:
        return categories[slug]
    color_map = {
        "announcements": "0088CC",
        "support": "2E8B57",
        "product": "B55400",
    }
    payload = api_json(
        "POST",
        "categories.json",
        data={
            "name": name,
            "color": color_map.get(slug, "6E6E6E"),
            "text_color": "FFFFFF",
        },
    )
    category = payload.get("category") if isinstance(payload, dict) else None
    if not category:
        raise RuntimeError(f"failed to create category: {name}")
    return category


def latest_topics() -> List[Dict]:
    payload = api_json("GET", "latest.json") or {}
    return (payload.get("topic_list") or {}).get("topics", [])


def find_topic(title: str, category_id: int) -> Optional[Dict]:
    for topic in latest_topics():
        if topic.get("title") == title and int(topic.get("category_id", 0)) == int(category_id):
            return topic
    return None


def topic_payload(topic_id: int) -> Dict:
    return api_json("GET", f"t/{int(topic_id)}.json") or {}


def topic_raw_posts(topic_id: int) -> Set[str]:
    payload = topic_payload(topic_id)
    posts = payload.get("post_stream", {}).get("posts", [])
    return {str(post.get("raw", "")).strip() for post in posts if isinstance(post, dict)}


def create_topic(title: str, raw: str, category_id: int, username: str) -> Dict:
    last_exc = None
    for _ in range(4):
        try:
            payload = api_json(
                "POST",
                "posts.json",
                username=username,
                data={
                    "title": title,
                    "raw": raw,
                    "category": category_id,
                },
            )
            time.sleep(1)
            return payload
        except RuntimeError as exc:
            last_exc = exc
            message = str(exc).lower()
            if "too similar to what you recently posted" in message:
                time.sleep(2)
                continue
            if "too quickly" in message or "wait 10 seconds" in message or "rate_limit" in message:
                time.sleep(11)
                continue
            if "too similar to what you recently posted" not in message:
                raise
    raise last_exc


def create_reply(topic_id: int, raw: str, username: str) -> Dict:
    last_exc = None
    for _ in range(4):
        try:
            payload = api_json(
                "POST",
                "posts.json",
                username=username,
                data={
                    "topic_id": int(topic_id),
                    "raw": raw,
                },
            )
            time.sleep(1)
            return payload
        except RuntimeError as exc:
            last_exc = exc
            message = str(exc).lower()
            if "too similar to what you recently posted" in message:
                time.sleep(2)
                continue
            if "too quickly" in message or "wait 10 seconds" in message or "rate_limit" in message:
                time.sleep(11)
                continue
            if "too similar to what you recently posted" not in message:
                raise
    raise last_exc


def set_pinned(topic_id: int, pinned: bool) -> None:
    api(
        "PUT",
        f"t/{int(topic_id)}/status",
        data={"status": "pinned", "enabled": "true" if pinned else "false"},
    )


def set_closed(topic_id: int, closed: bool) -> None:
    api(
        "PUT",
        f"t/{int(topic_id)}/status",
        data={"status": "closed", "enabled": "true" if closed else "false"},
    )


def main():
    manifest = load_manifest()
    topic_summary = []

    for category in manifest.get("categories", []):
        name = category.get("name", "") if isinstance(category, dict) else str(category or "")
        if name:
            ensure_category(name)

    for topic in manifest.get("topics", []):
        category = ensure_category(topic["category"])
        existing = find_topic(topic["title"], int(category["id"]))
        if existing:
            topic_id = int(existing["id"])
        else:
            first_post = (topic.get("posts") or [])[0]
            if not first_post:
                raise RuntimeError(f"topic {topic['title']} missing first post")
            created = create_topic(topic["title"], first_post["raw"], int(category["id"]), first_post.get("username", API_USERNAME))
            topic_id = int(created["topic_id"])

        seen_posts = topic_raw_posts(topic_id)
        for reply in (topic.get("posts") or [])[1:]:
            raw = str(reply.get("raw", "")).strip()
            if not raw or raw in seen_posts:
                continue
            create_reply(topic_id, raw, reply.get("username", API_USERNAME))
            seen_posts.add(raw)

        set_pinned(topic_id, bool(topic.get("pinned", False)))
        set_closed(topic_id, bool(topic.get("closed", False)))
        topic_summary.append(
            {
                "title": topic["title"],
                "topic_id": topic_id,
                "category": topic["category"],
                "pinned": bool(topic.get("pinned", False)),
                "closed": bool(topic.get("closed", False)),
            }
        )

    print(json.dumps({"seeded_topics": topic_summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[seed_discourse_data] {exc}", file=sys.stderr)
        sys.exit(1)
