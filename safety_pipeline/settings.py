import os

REPO_ROOT = os.path.dirname(os.path.dirname(__file__))

# 自动加载项目根目录的 .env 文件
_env_path = os.path.join(REPO_ROOT, ".env")
if os.path.isfile(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                os.environ.setdefault(_key.strip(), _val.strip())

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "your_openai_api_key")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", None)
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.2")
DEFAULT_PIPELINE_ENV = "gitlab"
MAX_STEP_REPLAN = 2
MAX_CONVERSATION_TURNS = 8
MAX_DIALOGUE_SUMMARY_CHARS = 400
PLAN_MEMORY_TOP_K = 6
MAX_AGENT_TOOL_ROUNDS = 40
MAX_TOOL_CALL_RETRIES = 3

MEMORY_DIR = os.path.join(REPO_ROOT, "memory")
EXPERIENCE_MEMORY_PATH = os.path.join(MEMORY_DIR, "experience_memory.json")
TOOL_MEMORY_PATH = os.path.join(MEMORY_DIR, "tool_memory.json")
SFT_DATASET_PATH = os.path.join(MEMORY_DIR, "sft_dataset.jsonl")
LOCAL_EMBEDDING_MODEL = os.environ.get(
    "LOCAL_EMBEDDING_MODEL",
    "paraphrase-multilingual-MiniLM-L12-v2",
)
PLAN_MEMORY_FAISS_PATH = os.path.join(MEMORY_DIR, "plan_memory.faiss")
PLAN_MEMORY_META_PATH = os.path.join(MEMORY_DIR, "plan_memory_meta.json")


def get_pipeline_env():
    return os.environ.get("PIPELINE_ENV", DEFAULT_PIPELINE_ENV)


def set_pipeline_env(env_name):
    os.environ["PIPELINE_ENV"] = env_name
