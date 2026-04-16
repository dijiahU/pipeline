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
The input already contains the proposed real tool call from the main agent
under the field assistant_proposed_tool_call.
Do not repeat, rewrite, or modify the tool name or tool arguments.

Contracts:
- <|direct_execute|>{"reasoning":"..."}
- <|ask_human|>{"reasoning":"..."}
- <|refuse|>{"reasoning":"..."}
- <|replan|>{"reasoning":"..."}

Do not include markdown, explanations, or any text before the decision token."""


def decision_to_special_token(decision):
    return DECISION_TO_TOKEN.get(str(decision or "").strip(), "")


def _decision_reasoning(case):
    risk = case.get("risk") or {}
    reasoning = str(risk.get("reasoning") or risk.get("reason") or "").strip()
    if reasoning:
        return reasoning

    decision = str(case.get("decision") or "").strip()
    if decision == "refuse":
        reasoning = str(case.get("decision_reason") or "").strip()
        if reasoning:
            return reasoning
    if decision == "replan":
        reasoning = str(case.get("decision_reason") or "").strip()
        if reasoning:
            return reasoning
    return ""


def branch_payload_from_case(case):
    case = case or {}
    decision = str(case.get("decision") or "").strip()

    if decision == "direct_tool":
        return "direct_execute", {"reasoning": _decision_reasoning(case)}

    if decision == "ask_human":
        return "ask_human", {"reasoning": _decision_reasoning(case)}

    if decision == "refuse":
        return "refuse", {"reasoning": _decision_reasoning(case)}

    if decision == "replan":
        return "replan", {"reasoning": _decision_reasoning(case)}

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
