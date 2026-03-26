import copy
import json
import os
import tempfile

from .settings import REPO_ROOT


def default_state_path(service_id):
    return os.path.join(REPO_ROOT, "memory", f"{service_id}_state.json")


def default_seed_manifest_path(service_id):
    return os.path.join(REPO_ROOT, "docker", service_id, "seed_manifest.json")


def _deepcopy_payload(payload):
    if payload is None:
        return {}
    return copy.deepcopy(payload)


def write_json_state(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix="service-state-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def load_json_state(path, seed_path=None, default_state=None):
    if not os.path.isfile(path):
        if seed_path and os.path.isfile(seed_path):
            with open(seed_path, "r", encoding="utf-8") as fh:
                seeded = json.load(fh)
            write_json_state(path, seeded)
            return _deepcopy_payload(seeded)
        seeded = _deepcopy_payload(default_state)
        write_json_state(path, seeded)
        return seeded

    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
