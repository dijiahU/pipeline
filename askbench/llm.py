"""Minimal OpenAI-compatible LLM caller with tool_choice=required."""

import json
import os

from openai import OpenAI

from config import MAX_LLM_RETRIES, MAX_TOKENS, REQUEST_TIMEOUT, TEMPERATURE


def _get_client(model_config: dict) -> OpenAI:
    return OpenAI(
        base_url=model_config.get("base_url") or os.getenv("OPENAI_BASE_URL"),
        api_key=model_config.get("api_key") or os.getenv("OPENAI_API_KEY"),
        timeout=REQUEST_TIMEOUT,
    )


def call_with_tools(
    model_config: dict,
    system_prompt: str,
    user_message: str,
    tools: list[dict],
) -> dict | None:
    """Call LLM with tool_choice=required. Returns {"name": str, "arguments": dict} or None."""
    client = _get_client(model_config)
    model = model_config.get("model", "gpt-5.4")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    for attempt in range(MAX_LLM_RETRIES + 1):
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="required",
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
        )
        message = response.choices[0].message
        if message.tool_calls:
            tc = message.tool_calls[0]
            try:
                args = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                args = {}
            return {"name": tc.function.name, "arguments": args}

        # No tool call — retry with feedback
        text = (message.content or "").strip()
        messages.append({"role": "assistant", "content": text or "[no tool call]"})
        messages.append({
            "role": "user",
            "content": (
                "Your previous response did not include a tool call. "
                "You must call exactly one tool from the provided tools list."
            ),
        })

    return None


def call_with_tools_multi_turn(
    model_config: dict,
    system_prompt: str,
    messages: list[dict],
    tools: list[dict],
) -> dict | None:
    """Multi-turn variant: caller manages message history. Returns tool_call or None."""
    client = _get_client(model_config)
    model = model_config.get("model", "gpt-5.4")

    full_messages = [{"role": "system", "content": system_prompt}] + messages

    for attempt in range(MAX_LLM_RETRIES + 1):
        response = client.chat.completions.create(
            model=model,
            messages=full_messages,
            tools=tools,
            tool_choice="required",
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
        )
        message = response.choices[0].message
        if message.tool_calls:
            tc = message.tool_calls[0]
            try:
                args = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                args = {}
            return {"name": tc.function.name, "arguments": args}

        text = (message.content or "").strip()
        full_messages.append({"role": "assistant", "content": text or "[no tool call]"})
        full_messages.append({
            "role": "user",
            "content": (
                "Your previous response did not include a tool call. "
                "You must call exactly one tool from the provided tools list."
            ),
        })

    return None
