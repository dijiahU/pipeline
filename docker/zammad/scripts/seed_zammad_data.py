import json
import os
import subprocess
import sys
import time

try:
    import requests
    from requests.auth import HTTPBasicAuth
except ModuleNotFoundError as exc:
    raise SystemExit("requests 未安装，无法 seed Zammad 数据。") from exc


BASE_URL = os.environ.get("ZAMMAD_BASE_URL", "http://localhost:8081").rstrip("/")
ADMIN_USER = os.environ.get("ZAMMAD_ADMIN_USER", "admin@example.com")
ADMIN_PASSWORD = os.environ.get("ZAMMAD_ADMIN_PASSWORD", "Admin123!")
MANIFEST_PATH = os.environ.get("ZAMMAD_SEED_MANIFEST")
RAILS_CONTAINER = os.environ.get("ZAMMAD_RAILSSERVER_CONTAINER", "pipeline-zammad-railsserver")


def api(method, path, **kwargs):
    url = f"{BASE_URL}/api/v1/{path.lstrip('/')}"
    kwargs.setdefault("auth", HTTPBasicAuth(ADMIN_USER, ADMIN_PASSWORD))
    kwargs.setdefault("timeout", 30)
    kwargs.setdefault("headers", {})
    headers = kwargs["headers"]
    if "Content-Type" not in headers and method.upper() in {"POST", "PUT", "PATCH"}:
        headers["Content-Type"] = "application/json"
    resp = requests.request(method, url, **kwargs)
    if resp.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> {resp.status_code}: {resp.text[:800]}")
    if not resp.text:
        return None
    try:
        return resp.json()
    except Exception:
        return resp.text


def wait_for_api():
    deadline = time.time() + 240
    while time.time() < deadline:
        try:
            resp = requests.get(
                f"{BASE_URL}/api/v1/users/me",
                auth=HTTPBasicAuth(ADMIN_USER, ADMIN_PASSWORD),
                timeout=10,
            )
            if resp.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(5)
    raise RuntimeError("等待 Zammad API 就绪超时")


def load_manifest():
    if not MANIFEST_PATH:
        raise RuntimeError("未提供 ZAMMAD_SEED_MANIFEST")
    with open(MANIFEST_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def ensure_group(name):
    for group in api("GET", "groups") or []:
        if group.get("name") == name:
            return group
    return api(
        "POST",
        "groups",
        json={
            "name": name,
            "assignment_timeout": 0,
            "follow_up_possible": "yes",
            "follow_up_assignment": True,
            "active": True,
        },
    )


def ensure_customer(customer):
    for user in api("GET", "users") or []:
        if user.get("email") == customer["email"]:
            return user
    payload = {
        "firstname": customer["firstname"],
        "lastname": customer["lastname"],
        "email": customer["email"],
        "login": customer.get("login", customer["email"]),
        "roles": ["Customer"],
    }
    return api("POST", "users", json=payload)


def ensure_ticket(ticket):
    for existing in api("GET", "tickets") or []:
        if existing.get("title") == ticket["title"]:
            return existing
    created = api(
        "POST",
        "tickets",
        json={
            "title": ticket["title"],
            "group": ticket["group"],
            "customer": ticket["customer_email"],
            "priority": ticket.get("priority", "2 normal"),
            "article": {
                "subject": ticket.get("subject", ticket["title"]),
                "body": ticket["body"],
                "type": "note",
                "internal": False,
            },
        },
    )
    desired_state = ticket.get("state", "open")
    if desired_state:
        api("PUT", f"tickets/{created['id']}", json={"state": desired_state})
    return api("GET", f"tickets/{created['id']}")


def grant_admin_group_access(group_names):
    ruby_group_map = ", ".join([f"\"{name}\" => [\"full\"]" for name in group_names])
    ruby = (
        f'user = User.find_by!(email: "{ADMIN_USER}"); '
        f'user.group_names_access_map = {{{ruby_group_map}}}; '
        "user.save!; "
        "puts user.group_names_access_map.inspect"
    )
    result = subprocess.run(
        [
            "docker",
            "exec",
            RAILS_CONTAINER,
            "bash",
            "-lc",
            f"bundle exec rails r '{ruby}'",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(f"grant_admin_group_access failed: {detail}")


def main():
    wait_for_api()
    manifest = load_manifest()

    expected_titles = {ticket["title"] for ticket in manifest.get("tickets", [])}
    for ticket in api("GET", "tickets") or []:
        if ticket.get("title") not in expected_titles:
            api("DELETE", f"tickets/{ticket['id']}")

    for group_name in manifest.get("groups", []):
        ensure_group(group_name)
    grant_admin_group_access(["Users"] + manifest.get("groups", []))

    created_customers = {}
    for customer in manifest.get("customers", []):
        created_customers[customer["email"]] = ensure_customer(customer)

    seeded_tickets = []
    for ticket in manifest.get("tickets", []):
        seeded_tickets.append(ensure_ticket(ticket))

    result = {
        "seeded_groups": manifest.get("groups", []),
        "seeded_customers": [customer["email"] for customer in manifest.get("customers", [])],
        "seeded_tickets": [
            {
                "id": ticket.get("id"),
                "title": ticket.get("title"),
                "state_id": ticket.get("state_id"),
            }
            for ticket in seeded_tickets
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[seed_zammad_data] {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
