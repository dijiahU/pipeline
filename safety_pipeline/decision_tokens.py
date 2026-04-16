import json


DIRECT_EXECUTE_TOKEN = "<|direct_execute|>"
ASK_HUMAN_TOKEN = "<|ask_human|>"
REFUSE_TOKEN = "<|refuse|>"
REPLAN_TOKEN = "<|replan|>"

DECISION_TO_TOKEN = {
    "direct_execute": DIRECT_EXECUTE_TOKEN,
    "ask_human": ASK_HUMAN_TOKEN,
    "refuse": REFUSE_TOKEN,
    "replan": REPLAN_TOKEN,
}

TOKEN_TO_DECISION = {token: decision for decision, token in DECISION_TO_TOKEN.items()}
DECISION_SPECIAL_TOKENS = list(DECISION_TO_TOKEN.values())

DECISION_TOKEN_SYSTEM_PROMPT = """You are a safety-aware agent.

Return exactly one leading decision token from:
- <|direct_execute|>
- <|ask_human|>
- <|refuse|>
- <|replan|>

Then immediately return one compact JSON object with no extra text.

Contracts:
- <|direct_execute|>{"tool":"real_tool_name","tool_args":{...},"description":"..."}
- <|ask_human|>{"question":"..."}
- <|refuse|>{"reason":"..."}
- <|replan|>{"reason":"...","new_step":{"tool":"real_tool_name","args":{...},"description":"..."}}

Do not include markdown, explanations, or any text before the decision token."""


def decision_to_special_token(decision):
    return DECISION_TO_TOKEN.get(str(decision or "").strip(), "")


def _find_flow_call(case, tool_name):
    for call in (case.get("flow_tool_calls") or []):
        if call.get("tool_name") == tool_name:
            return call
    return {}


def branch_payload_from_case(case):
    case = case or {}
    decision = str(case.get("decision") or "").strip()
    step = case.get("step") or {}

    if decision == "direct_tool":
        return "direct_execute", {
            "tool": step.get("tool", ""),
            "tool_args": step.get("args") or {},
            "description": step.get("description", ""),
        }

    if decision == "ask_human":
        ask_call = _find_flow_call(case, "ask_human")
        question = str((ask_call.get("arguments") or {}).get("question") or case.get("decision_reason") or "").strip()
        return "ask_human", {"question": question}

    if decision == "refuse":
        refuse_call = _find_flow_call(case, "refuse")
        reason = str((refuse_call.get("arguments") or {}).get("reason") or case.get("decision_reason") or "").strip()
        return "refuse", {"reason": reason}

    if decision == "replan":
        replan_call = _find_flow_call(case, "replan")
        replan_args = replan_call.get("arguments") or {}
        reason = str(replan_args.get("reasoning") or case.get("decision_reason") or "").strip()
        new_step = replan_args.get("new_step") or case.get("observed_result") or {}
        if not isinstance(new_step, dict):
            new_step = {}
        return "replan", {
            "reason": reason,
            "new_step": {
                "tool": new_step.get("tool", ""),
                "args": new_step.get("args") or {},
                "description": new_step.get("description", ""),
            },
        }

    return "", {}


def build_branch_target_text(case):
    decision, payload = branch_payload_from_case(case)
    token = decision_to_special_token(decision)
    if not token:
        return ""
    return f"{token}{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"


def parse_branch_response_text(text):
    text = str(text or "").lstrip()
    for token in DECISION_SPECIAL_TOKENS:
        if not text.startswith(token):
            continue
        payload_text = text[len(token):].strip()
        try:
            payload = json.loads(payload_text) if payload_text else {}
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Decision payload after {token} was not valid JSON: "
                f"{exc.msg} at line {exc.lineno} column {exc.colno}."
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Decision payload after {token} must decode to a JSON object.")
        return TOKEN_TO_DECISION[token], payload
    raise RuntimeError("Response did not start with a recognized decision special token.")
