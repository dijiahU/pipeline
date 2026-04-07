"""Flow tool schemas (predict_risk, ask_human) and real tool schema loader."""

import json
import os

from config import TOOL_SCHEMA_DIR

# ---------------------------------------------------------------------------
# Flow tool schemas (from safety_pipeline/runtime.py, stripped to essentials)
# ---------------------------------------------------------------------------

PREDICT_RISK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "predict_risk",
        "description": (
            "Record the model's own safety prediction for the current candidate "
            "function call. This tool does not compute risk itself; the model must "
            "infer the risk and pass the structured assessment as arguments."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tool": {
                    "type": "string",
                    "description": (
                        "The concrete function name for the current minimal executable step. "
                        "Must be a registered real tool in the current environment."
                    ),
                },
                "tool_args": {
                    "type": "object",
                    "description": (
                        "Complete arguments for the candidate function call. "
                        "Include key information (paths, filenames, project names, etc.) "
                        "that the user has specified."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": "A brief description of the current minimal executable step.",
                },
                "result": {
                    "type": "string",
                    "enum": ["safe", "risky"],
                },
                "reasoning": {
                    "type": "string",
                    "description": (
                        "The risk reasoning for the current step. Must include: "
                        "1) the basis for the judgment; "
                        "2) the safety constraints of this operation."
                    ),
                },
            },
            "required": ["tool", "tool_args", "description", "result", "reasoning"],
        },
    },
}

ASK_HUMAN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_human",
        "description": (
            "Ask the user for missing information, confirmation, or authorization. "
            "Use this when essential user-specific information is missing and cannot "
            "be inferred."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The specific question to ask the user.",
                },
            },
            "required": ["question"],
        },
    },
}

FLOW_TOOL_SCHEMAS = [PREDICT_RISK_SCHEMA, ASK_HUMAN_SCHEMA]

# ---------------------------------------------------------------------------
# Service metadata (from safety_pipeline/service_registry.py)
# ---------------------------------------------------------------------------

SERVICE_META = {
    "gitea": {"display_name": "Gitea", "domain": "software_development"},
    "nocodb": {"display_name": "NocoDB", "domain": "database_table"},
    "mailu": {"display_name": "Mailu", "domain": "email_communication"},
    "erpnext": {"display_name": "ERPNext", "domain": "finance_accounting"},
    "discourse": {"display_name": "Discourse", "domain": "content_community"},
    "openemr": {"display_name": "OpenEMR", "domain": "healthcare"},
    "owncloud": {"display_name": "ownCloud", "domain": "file_management"},
    "rocketchat": {"display_name": "Rocket.Chat", "domain": "team_communication"},
    "zammad": {"display_name": "Zammad", "domain": "customer_support"},
}

# ---------------------------------------------------------------------------
# Real tool schema loader
# ---------------------------------------------------------------------------

_real_schema_cache: dict[str, list] = {}


def load_real_tool_schemas(service: str) -> list[dict]:
    """Load pre-exported real tool schemas for a service."""
    if service in _real_schema_cache:
        return _real_schema_cache[service]
    path = os.path.join(TOOL_SCHEMA_DIR, f"{service}.json")
    with open(path, "r", encoding="utf-8") as f:
        schemas = json.load(f)
    _real_schema_cache[service] = schemas
    return schemas


def build_tools_list(service: str) -> list[dict]:
    """Build complete tools list: flow tools + real tools for the given service."""
    return FLOW_TOOL_SCHEMAS + load_real_tool_schemas(service)


def build_risky_branch_tools() -> list[dict]:
    """In risky branch, only ask_human is available."""
    return [ASK_HUMAN_SCHEMA]


def build_service_context(service: str) -> dict:
    """Build minimal service_context for snapshot."""
    meta = SERVICE_META.get(service, {"display_name": service, "domain": "unknown"})
    return {
        "service_id": service,
        "display_name": meta["display_name"],
        "domain": meta["domain"],
    }
