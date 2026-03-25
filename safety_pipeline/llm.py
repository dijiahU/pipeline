import json

from .settings import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL

try:
    import openai
except ModuleNotFoundError:
    openai = None


client = None


def get_openai_client():
    global client
    if client is not None:
        return client
    if openai is None:
        raise RuntimeError("当前环境未安装 openai，无法运行 pipeline 决策流程。")
    kwargs = {"api_key": OPENAI_API_KEY}
    if OPENAI_BASE_URL:
        kwargs["base_url"] = OPENAI_BASE_URL
    client = openai.OpenAI(**kwargs)
    return client


def call_json(system_prompt, user_payload):
    llm_client = get_openai_client()
    response = llm_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_payload},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


def call_json_or_text(prompt, user_payload=None):
    llm_client = get_openai_client()
    messages = [{"role": "system", "content": prompt}]
    if user_payload:
        messages.append({"role": "user", "content": user_payload})
    response = llm_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=messages,
    )
    return response.choices[0].message.content.strip()


def call_required_tool_choice(system_prompt, snapshot, tools):
    llm_client = get_openai_client()
    response = llm_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(snapshot, ensure_ascii=False, indent=2)},
        ],
        tools=tools,
        tool_choice="required",
    )
    message = response.choices[0].message
    if not message.tool_calls:
        raise RuntimeError("模型未返回任何 tool call。")
    return message.tool_calls[0]


def call_auto_tool_choice(system_prompt, snapshot, tools):
    """tool_choice=auto: 模型可以选择调用工具，也可以直接回复文本。

    返回 (tool_call, None) 或 (None, text_content)。
    """
    llm_client = get_openai_client()
    response = llm_client.chat.completions.create(
        model=OPENAI_MODEL,
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
