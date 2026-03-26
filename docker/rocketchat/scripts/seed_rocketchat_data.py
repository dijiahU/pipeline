#!/usr/bin/env python3
"""
Rocket.Chat 种子数据脚本。

通过 REST API 创建 admin、用户、频道和消息。
Rocket.Chat 首次启动后需要注册 admin 用户，然后用 admin 凭证创建其他数据。
"""

import json
import os
import time
from pathlib import Path

import requests

BASE_URL = os.environ.get("ROCKETCHAT_BASE_URL", "http://localhost:3100").rstrip("/")
ADMIN_USER = os.environ.get("ROCKETCHAT_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ROCKETCHAT_ADMIN_PASSWORD", "Admin123!")
ADMIN_EMAIL = os.environ.get("ROCKETCHAT_ADMIN_EMAIL", "admin@example.com")
MANIFEST_PATH = Path(
    os.environ.get(
        "ROCKETCHAT_SEED_MANIFEST",
        Path(__file__).resolve().parents[1] / "seed_manifest.json",
    )
)


class SeedError(RuntimeError):
    pass


_auth_cache = {"user_id": None, "token": None}


def _api(method, endpoint, json_data=None, auth=True):
    """Call Rocket.Chat REST API."""
    url = f"{BASE_URL}/api/v1/{endpoint.lstrip('/')}"
    headers = {"Content-Type": "application/json"}
    if auth and _auth_cache["token"]:
        headers["X-Auth-Token"] = _auth_cache["token"]
        headers["X-User-Id"] = _auth_cache["user_id"]
    resp = requests.request(method, url, json=json_data, headers=headers, timeout=30)
    return resp


def wait_for_rocketchat(max_wait=180, interval=3):
    """Wait for Rocket.Chat API to become ready."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            resp = requests.get(f"{BASE_URL}/api/info", timeout=5)
            if resp.status_code == 200:
                print("[seed] Rocket.Chat API is ready")
                return
        except Exception:
            pass
        time.sleep(interval)
    raise SeedError(f"Rocket.Chat API not ready after {max_wait}s")


def setup_admin():
    """Register admin user or login if already exists."""
    # Try login first
    resp = _api("POST", "login", {"user": ADMIN_USER, "password": ADMIN_PASSWORD}, auth=False)
    if resp.status_code == 200:
        data = resp.json().get("data", {})
        _auth_cache["user_id"] = data.get("userId", "")
        _auth_cache["token"] = data.get("authToken", "")
        print(f"[seed] Admin login successful (userId: {_auth_cache['user_id']})")
        return

    # Try registration
    resp = _api("POST", "users.register", {
        "username": ADMIN_USER,
        "email": ADMIN_EMAIL,
        "pass": ADMIN_PASSWORD,
        "name": "Administrator",
    }, auth=False)
    if resp.status_code == 200 and resp.json().get("success"):
        print("[seed] Admin user registered")
    else:
        # May already exist, try login again
        pass

    # Login
    resp = _api("POST", "login", {"user": ADMIN_USER, "password": ADMIN_PASSWORD}, auth=False)
    if resp.status_code != 200:
        raise SeedError(f"Admin login failed: {resp.status_code} {resp.text[:300]}")
    data = resp.json().get("data", {})
    _auth_cache["user_id"] = data.get("userId", "")
    _auth_cache["token"] = data.get("authToken", "")
    print(f"[seed] Admin login successful (userId: {_auth_cache['user_id']})")


def create_user(username, email, password, name, roles=None):
    """Create a user via admin API."""
    resp = _api("POST", "users.create", {
        "username": username,
        "email": email,
        "password": password,
        "name": name,
        "roles": roles or ["user"],
        "verified": True,
    })
    if resp.status_code == 200 and resp.json().get("success"):
        print(f"[seed]   User created: {username}")
        return resp.json().get("user", {}).get("_id", "")
    # May already exist
    error_msg = resp.json().get("error", "") if resp.status_code == 400 else ""
    if "already in use" in error_msg.lower() or "is already taken" in error_msg.lower():
        print(f"[seed]   User exists: {username}")
        # Look up user ID
        lookup = _api("GET", f"users.info?username={username}")
        if lookup.status_code == 200:
            return lookup.json().get("user", {}).get("_id", "")
        return ""
    print(f"[seed]   User creation for {username}: {resp.status_code} {resp.text[:200]}")
    return ""


def create_channel(name, members=None, topic="", description="", private=False):
    """Create a channel or group."""
    endpoint = "groups.create" if private else "channels.create"
    payload = {"name": name, "members": members or []}
    resp = _api("POST", endpoint, payload)

    if resp.status_code == 200 and resp.json().get("success"):
        ch = resp.json().get("channel" if not private else "group", {})
        room_id = ch.get("_id", "")
        print(f"[seed]   {'Group' if private else 'Channel'} created: #{name}")
    else:
        error_msg = resp.json().get("error", "") if resp.status_code == 400 else ""
        if "already exists" in error_msg.lower() or "name is already" in error_msg.lower() or "duplicate" in error_msg.lower():
            print(f"[seed]   {'Group' if private else 'Channel'} exists: #{name}")
            # Look up room ID
            info_endpoint = "groups.info" if private else "channels.info"
            lookup = _api("GET", f"{info_endpoint}?roomName={name}")
            if lookup.status_code == 200:
                key = "group" if private else "channel"
                room_id = lookup.json().get(key, {}).get("_id", "")
            else:
                return ""
        else:
            print(f"[seed]   Channel creation for #{name}: {resp.status_code} {resp.text[:200]}")
            return ""

    # Set topic and description
    set_endpoint = "groups" if private else "channels"
    if topic:
        _api("POST", f"{set_endpoint}.setTopic", {"roomId": room_id, "topic": topic})
    if description:
        _api("POST", f"{set_endpoint}.setDescription", {"roomId": room_id, "description": description})

    return room_id


def send_message(channel_name, text, alias=None):
    """Send a message to a channel using chat.postMessage (admin, supports alias)."""
    payload = {"channel": f"#{channel_name}", "text": text}
    if alias:
        payload["alias"] = alias

    resp = _api("POST", "chat.postMessage", json_data=payload)
    if resp.status_code == 200 and resp.json().get("success"):
        return
    # Fallback for private groups
    payload["channel"] = channel_name
    resp = _api("POST", "chat.postMessage", json_data=payload)
    if resp.status_code == 200 and resp.json().get("success"):
        return
    print(f"[seed]   Message send to #{channel_name}: {resp.status_code} {resp.text[:200]}")


def login_as(username, password):
    """Login as a specific user, returning (user_id, token)."""
    resp = _api("POST", "login", {"user": username, "password": password}, auth=False)
    if resp.status_code == 200:
        data = resp.json().get("data", {})
        return data.get("userId", ""), data.get("authToken", "")
    return None, None


def seed_from_manifest(manifest_path):
    with open(manifest_path) as f:
        manifest = json.load(f)

    # Create users
    user_passwords = {}
    for user_spec in manifest.get("users", []):
        create_user(
            user_spec["username"],
            user_spec["email"],
            user_spec["password"],
            user_spec["name"],
            user_spec.get("roles", ["user"]),
        )
        user_passwords[user_spec["username"]] = user_spec["password"]

    # Admin password
    admin_spec = manifest.get("admin", {})
    user_passwords[admin_spec.get("username", "admin")] = admin_spec.get("password", ADMIN_PASSWORD)

    # Create channels
    for ch_spec in manifest.get("channels", []):
        create_channel(
            ch_spec["name"],
            members=ch_spec.get("members", []),
            topic=ch_spec.get("topic", ""),
            description=ch_spec.get("description", ""),
            private=ch_spec.get("private", False),
        )

    # Send messages — all via admin using postMessage
    for msg_spec in manifest.get("messages", []):
        sender = msg_spec["sender"]
        channel = msg_spec["channel"]
        for text in msg_spec.get("texts", []):
            # Prefix with sender name so messages have attribution
            prefixed = f"[{sender}] {text}" if sender != ADMIN_USER else text
            send_message(channel, prefixed)
        print(f"[seed]   Sent {len(msg_spec['texts'])} messages to #{channel} as {sender}")


def main():
    wait_for_rocketchat()
    setup_admin()
    print(f"[seed] Seeding Rocket.Chat as user '{ADMIN_USER}'")
    seed_from_manifest(MANIFEST_PATH)
    print("[seed] Rocket.Chat seeding complete")


if __name__ == "__main__":
    main()
