import json
from types import SimpleNamespace

from .settings import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MAX_TOKENS, OPENAI_MODEL

try:
    import openai
except ModuleNotFoundError:
    openai = None


client = None


def _base_request_kwargs():
    kwargs = {"model": OPENAI_MODEL}
    if OPENAI_MAX_TOKENS and OPENAI_MAX_TOKENS > 0:
        kwargs["max_tokens"] = OPENAI_MAX_TOKENS
    return kwargs


def get_openai_client():
    global client
    if client is not None:
        return client
    if openai is None:
        raise RuntimeError("openai is not installed in the current environment, so the pipeline cannot run.")
    kwargs = {"api_key": OPENAI_API_KEY}
    if OPENAI_BASE_URL:
        kwargs["base_url"] = OPENAI_BASE_URL
    client = openai.OpenAI(**kwargs)
    return client


def _extract_message_text(message):
    if message is None:
        return ""

    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                text_value = item.get("text")
                if isinstance(text_value, str):
                    parts.append(text_value)
                    continue
                if isinstance(text_value, dict):
                    nested = text_value.get("value")
                    if isinstance(nested, str):
                        parts.append(nested)
                        continue
                for key in ("input_text", "output_text", "value"):
                    nested = item.get(key)
                    if isinstance(nested, str):
                        parts.append(nested)
                        break
                continue
            text_value = getattr(item, "text", None)
            if isinstance(text_value, str):
                parts.append(text_value)
                continue
            if text_value is not None:
                nested = getattr(text_value, "value", None)
                if isinstance(nested, str):
                    parts.append(nested)
                    continue
            value = getattr(item, "value", None)
            if isinstance(value, str):
                parts.append(value)
        return "\n".join(part.strip() for part in parts if str(part).strip()).strip()

    for attr in ("parsed", "refusal"):
        value = getattr(message, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""


def _extract_response_text(response):
    if response is None:
        return ""
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    return _extract_message_text(message)


def _parse_json_response_text(text):
    text = str(text or "").strip()
    if not text:
        return None
    candidates = [text]
    if "```" in text:
        for chunk in text.split("```"):
            chunk = chunk.strip()
            if not chunk:
                continue
            if chunk.lower().startswith("json"):
                chunk = chunk[4:].strip()
            candidates.append(chunk)
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return None


def _tool_names_from_schemas(tools):
    names = []
    for item in tools or []:
        function = (item or {}).get("function") or {}
        name = str(function.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _normalize_tool_name(name, allowed_names):
    tool_name = str(name or "").strip()
    if not tool_name:
        return ""
    if not allowed_names or tool_name in allowed_names:
        return tool_name
    for prefix in ("functions.", "function.", "tools."):
        if tool_name.startswith(prefix):
            candidate = tool_name[len(prefix):].strip()
            if candidate in allowed_names:
                return candidate
    return tool_name


def _build_synthetic_tool_call(name, arguments):
    normalized_args = arguments if isinstance(arguments, dict) else {}
    return SimpleNamespace(
        function=SimpleNamespace(
            name=str(name or "").strip(),
            arguments=json.dumps(normalized_args, ensure_ascii=False),
        )
    )


def _summarize_invalid_tool_payload(payload, limit=280):
    text = str(payload or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _validate_tool_call(tool_call, allowed_tool_names):
    if tool_call is None:
        return None, "No tool call was returned."

    allowed_name_set = set(allowed_tool_names or [])
    function = getattr(tool_call, "function", None)
    raw_name = getattr(function, "name", "")
    tool_name = _normalize_tool_name(raw_name, allowed_name_set)
    if not tool_name:
        return None, "The tool call did not include a tool name."
    if allowed_name_set and tool_name not in allowed_name_set:
        return None, f"Tool '{tool_name}' is not in the available tools list."

    raw_arguments = getattr(function, "arguments", None)
    if raw_arguments in (None, ""):
        arguments = {}
    elif isinstance(raw_arguments, dict):
        arguments = raw_arguments
    elif isinstance(raw_arguments, str):
        arguments = _parse_json_response_text(raw_arguments)
        if arguments is None:
            preview = _summarize_invalid_tool_payload(raw_arguments)
            return None, f"Tool '{tool_name}' returned invalid JSON arguments: {preview}"
    else:
        return None, (
            f"Tool '{tool_name}' returned unsupported arguments type "
            f"{type(raw_arguments).__name__}; expected a JSON object string."
        )

    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return None, f"Tool '{tool_name}' arguments must be a JSON object."

    return _build_synthetic_tool_call(tool_name, arguments), ""


def _build_tool_choice_fallback_payload(snapshot, tools, last_error):
    compact_tools = []
    for item in tools or []:
        function = (item or {}).get("function") or {}
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        compact_tools.append(
            {
                "name": name,
                "description": function.get("description", ""),
                "parameters": function.get("parameters", {}),
            }
        )
    return {
        "snapshot": snapshot,
        "available_tools": compact_tools,
        "last_error": str(last_error or "").strip(),
        "response_contract": {
            "tool_name": "must be exactly one of available_tools[].name",
            "arguments": "must be a JSON object matching the selected tool schema",
        },
    }


def _fallback_to_json_tool_choice(system_prompt, snapshot, tools, last_error=""):
    llm_client = get_openai_client()
    response = llm_client.chat.completions.create(
        **_base_request_kwargs(),
        messages=[
            {
                "role": "system",
                "content": (
                    f"{system_prompt}\n\n"
                    "Tool-calling fallback mode: do not emit an API tool call in this response. "
                    "Instead, return exactly one JSON object with keys tool_name and arguments. "
                    "tool_name must be one of the provided tools. arguments must be a JSON object. "
                    "Return JSON only."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    _build_tool_choice_fallback_payload(snapshot, tools, last_error),
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        ],
        response_format={"type": "json_object"},
    )
    parsed = _parse_json_response_text(_extract_response_text(response))
    if not isinstance(parsed, dict):
        return None, "The JSON fallback did not return a JSON object."

    fallback_call = _build_synthetic_tool_call(
        parsed.get("tool_name") or parsed.get("name") or "",
        parsed.get("arguments"),
    )
    return _validate_tool_call(fallback_call, _tool_names_from_schemas(tools))


def call_json(system_prompt, user_payload):
    llm_client = get_openai_client()
    response = llm_client.chat.completions.create(
        **_base_request_kwargs(),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_payload},
        ],
        response_format={"type": "json_object"},
    )
    parsed = _parse_json_response_text(_extract_response_text(response))
    if parsed is None:
        raise RuntimeError("The model did not return a valid JSON object.")
    return parsed


def call_json_or_text(prompt, user_payload=None):
    llm_client = get_openai_client()
    messages = [{"role": "system", "content": prompt}]
    if user_payload:
        messages.append({"role": "user", "content": user_payload})
    response = llm_client.chat.completions.create(
        **_base_request_kwargs(),
        messages=messages,
    )
    return _extract_response_text(response)


def call_required_tool_choice(system_prompt, snapshot, tools, max_attempts=3):
    llm_client = get_openai_client()
    allowed_tool_names = _tool_names_from_schemas(tools)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(snapshot, ensure_ascii=False, indent=2)},
    ]
    last_text = ""
    last_error = ""
    for attempt in range(max_attempts):
        response = llm_client.chat.completions.create(
            **_base_request_kwargs(),
            messages=messages,
            tools=tools,
            tool_choice="required",
        )
        message = response.choices[0].message
        if message.tool_calls:
            validated_tool_call, error = _validate_tool_call(message.tool_calls[0], allowed_tool_names)
            if validated_tool_call is not None:
                return validated_tool_call
            last_error = error

        else:
            last_error = "No tool call was returned."

        last_text = _extract_message_text(message) or _extract_response_text(response)
        messages.append(
            {
                "role": "assistant",
                "content": last_text or f"[invalid tool call: {last_error}]",
            }
        )
        messages.append(
            {
                "role": "user",
                "content": (
                    f"Your previous response was invalid: {last_error} "
                    "Respond with exactly one tool call from the provided tools. "
                    "The tool name must come from the provided tools list, and the arguments must be a valid JSON object. "
                    "Do not answer with plain text."
                ),
            }
        )

    fallback_tool_call, fallback_error = _fallback_to_json_tool_choice(
        system_prompt,
        snapshot,
        tools,
        last_error=last_error,
    )
    if fallback_tool_call is not None:
        return fallback_tool_call

    details = []
    if last_error:
        details.append(f"Last error: {last_error}")
    if fallback_error:
        details.append(f"Fallback error: {fallback_error}")
    if last_text:
        details.append(f"Response text: {last_text}")
    detail = f" {' '.join(details)}" if details else ""
    raise RuntimeError(f"The model did not return a valid tool call.{detail}")


def call_auto_tool_choice(system_prompt, snapshot, tools):
    """tool_choice=auto: the model may either call a tool or reply with text.

    Returns (tool_call, None) or (None, text_content).
    """
    llm_client = get_openai_client()
    response = llm_client.chat.completions.create(
        **_base_request_kwargs(),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(snapshot, ensure_ascii=False, indent=2)},
        ],
        tools=tools,
        tool_choice="auto",
    )
    message = response.choices[0].message
    if message.tool_calls:
        return message.tool_calls[0], None
    return None, (message.content or "").strip()
