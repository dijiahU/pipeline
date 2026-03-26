#!/usr/bin/env python3
"""
ownCloud (oCIS) 种子数据脚本。

通过 WebDAV 创建目录和文件，通过 OCS API 创建共享链接。
oCIS 使用 HTTPS + Basic Auth。
"""

import json
import os
import time
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth

BASE_URL = os.environ.get("OWNCLOUD_BASE_URL", "https://localhost:9200").rstrip("/")
ADMIN_USER = os.environ.get("OWNCLOUD_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("OWNCLOUD_ADMIN_PASSWORD", "Admin123!")
MANIFEST_PATH = Path(
    os.environ.get(
        "OWNCLOUD_SEED_MANIFEST",
        Path(__file__).resolve().parents[1] / "seed_manifest.json",
    )
)

# oCIS uses self-signed certs in dev mode
VERIFY_SSL = False

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class SeedError(RuntimeError):
    pass


def _auth():
    return HTTPBasicAuth(ADMIN_USER, ADMIN_PASSWORD)


def _webdav_url(path=""):
    return f"{BASE_URL}/dav/files/{ADMIN_USER}/{path.lstrip('/')}"


def wait_for_owncloud(max_wait=120, interval=3):
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            resp = requests.get(
                f"{BASE_URL}/.well-known/openid-configuration",
                verify=VERIFY_SSL, timeout=5,
            )
            if resp.status_code == 200:
                print("[seed] ownCloud oCIS API is ready")
                return
        except Exception:
            pass
        time.sleep(interval)
    raise SeedError(f"ownCloud API not ready after {max_wait}s")


def ensure_folder(path):
    url = _webdav_url(path)
    resp = requests.request("MKCOL", url, auth=_auth(), verify=VERIFY_SSL, timeout=15)
    if resp.status_code in (201, 405):
        # 201=created, 405=already exists
        return
    if resp.status_code == 409:
        # parent missing — shouldn't happen if we create in order
        raise SeedError(f"MKCOL {path} -> 409 Conflict (parent missing?)")
    raise SeedError(f"MKCOL {path} -> {resp.status_code}: {resp.text[:300]}")


def upload_file(path, content):
    url = _webdav_url(path)
    resp = requests.put(
        url, data=content.encode("utf-8"),
        auth=_auth(), verify=VERIFY_SSL, timeout=15,
        headers={"Content-Type": "application/octet-stream"},
    )
    if resp.status_code in (201, 204):
        return
    raise SeedError(f"PUT {path} -> {resp.status_code}: {resp.text[:300]}")


def file_exists(path):
    url = _webdav_url(path)
    resp = requests.request(
        "PROPFIND", url, auth=_auth(), verify=VERIFY_SSL, timeout=15,
        headers={"Depth": "0"},
    )
    return resp.status_code == 207


def create_public_share(path, name=""):
    """Create a public link share via OCS API."""
    url = f"{BASE_URL}/ocs/v2.php/apps/files_sharing/api/v1/shares"
    resp = requests.post(
        url,
        auth=_auth(),
        verify=VERIFY_SSL,
        timeout=15,
        headers={"OCS-APIREQUEST": "true", "Content-Type": "application/json"},
        json={
            "path": f"/{path.lstrip('/')}",
            "shareType": 3,  # 3 = public link
            "permissions": 1,  # 1 = read
            "name": name or path.split("/")[-1],
        },
    )
    if resp.status_code in (200, 201):
        print(f"[seed]   Created public share for: {path}")
        return
    # OCS may return errors in XML/JSON body even with 200
    print(f"[seed]   Share creation for {path}: HTTP {resp.status_code} (may need Graph API)")


def seed_from_manifest(manifest_path):
    with open(manifest_path) as f:
        manifest = json.load(f)

    # Create folders
    for folder in manifest.get("folders", []):
        ensure_folder(folder)
        print(f"[seed]   Folder: {folder}")

    # Upload files
    for file_spec in manifest.get("files", []):
        path = file_spec["path"]
        if file_exists(path):
            print(f"[seed]   File exists, skipping: {path}")
            continue
        upload_file(path, file_spec["content"])
        print(f"[seed]   Uploaded: {path}")

    # Create shares
    for share_spec in manifest.get("shares", []):
        create_public_share(
            share_spec["path"],
            share_spec.get("name", ""),
        )


def main():
    wait_for_owncloud()
    print(f"[seed] Seeding ownCloud as user '{ADMIN_USER}'")
    seed_from_manifest(MANIFEST_PATH)
    print("[seed] ownCloud seeding complete")


if __name__ == "__main__":
    main()
