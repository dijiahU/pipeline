"""AskBench configuration: model endpoints, paths, experiment settings."""

import os

# ---------------------------------------------------------------------------
# Load .env from project root (no dependency on python-dotenv)
# ---------------------------------------------------------------------------
_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
if os.path.isfile(_ENV_FILE):
    with open(_ENV_FILE) as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _key, _, _val = _line.partition("=")
            _key, _val = _key.strip(), _val.strip()
            if _key:
                os.environ[_key] = _val  # .env takes precedence

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TASK_DIR = os.path.join(BASE_DIR, "..", "tasks")
TOOL_SCHEMA_DIR = os.path.join(BASE_DIR, "tool_schemas")
RESULTS_DIR = os.path.join(BASE_DIR, "results")
TRACES_PATH = os.path.join(RESULTS_DIR, "traces.jsonl")
SFT_OUTPUT_PATH = os.path.join(RESULTS_DIR, "sft_train.json")

# ---------------------------------------------------------------------------
# Model configs  (override via env vars)
# ---------------------------------------------------------------------------
MODELS = {
    "gpt54": {
        "base_url": os.getenv("GPT54_BASE_URL", os.getenv("OPENAI_BASE_URL", "")),
        "api_key": os.getenv("GPT54_API_KEY", os.getenv("OPENAI_API_KEY", "")),
        "model": os.getenv("GPT54_MODEL", os.getenv("OPENAI_MODEL", "gpt-5.4")),
    },
    "qwen_base": {
        "base_url": os.getenv("QWEN_BASE_URL", ""),
        "api_key": os.getenv("QWEN_API_KEY", ""),
        "model": os.getenv("QWEN_BASE_MODEL", "qwen-7b"),
    },
    "qwen_sft": {
        "base_url": os.getenv("QWEN_SFT_BASE_URL", os.getenv("QWEN_BASE_URL", "")),
        "api_key": os.getenv("QWEN_SFT_API_KEY", os.getenv("QWEN_API_KEY", "")),
        "model": os.getenv("QWEN_SFT_MODEL", "qwen-7b-sft"),
    },
}

PROMPT_VARIANTS = ["bare", "explicit_rules"]

# ---------------------------------------------------------------------------
# Experiment settings
# ---------------------------------------------------------------------------
TRAIN_COUNT = 185          # tasks for SFT trace generation
SPLIT_SEED = 42            # reproducible train/test split
MAX_LLM_RETRIES = 2        # retries per LLM call on invalid tool response
REQUEST_TIMEOUT = 60        # seconds
MAX_TOKENS = 1024
TEMPERATURE = 0.0
