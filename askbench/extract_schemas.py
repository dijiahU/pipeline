"""One-time script: export real tool schemas from each service's *_tools.py to JSON files."""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

SERVICES = [
    "gitea",
    "nocodb",
    "mailu",
    "erpnext",
    "discourse",
    "openemr",
    "owncloud",
    "rocketchat",
    "zammad",
]

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "tool_schemas")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for service in SERVICES:
        module_name = f"safety_pipeline.{service}_tools"
        try:
            mod = __import__(module_name, fromlist=["get_all_schemas"])
            schemas = mod.get_all_schemas()
            out_path = os.path.join(OUTPUT_DIR, f"{service}.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(schemas, f, ensure_ascii=False, indent=2)
            print(f"[OK] {service}: {len(schemas)} tools -> {out_path}")
        except Exception as e:
            print(f"[FAIL] {service}: {e}")


if __name__ == "__main__":
    main()
