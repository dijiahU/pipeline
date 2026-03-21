import json


def print_divider(char="=", width=60):
    print(char * width)


def print_stage_start(title):
    print(f"\n[阶段开始] {title}")
    print_divider("=")


def print_stage_end(title, summary=""):
    print_divider("-")
    suffix = f" -> {summary}" if summary else ""
    print(f"[阶段结束] {title}{suffix}")


def print_json_block(label, payload):
    print(f"[{label}]")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
