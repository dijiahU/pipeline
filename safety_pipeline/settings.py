import os


GENERATED_ENV_FILES = (
    ".env.gitea.generated",
    ".env.discourse.generated",
    ".env.erpnext.generated",
    ".env.mailu.generated",
    ".env.nocodb.generated",
    ".env.openemr.generated",
    ".env.owncloud.generated",
    ".env.rocketchat.generated",
    ".env.zammad.generated",
)

REPO_ROOT = os.path.dirname(os.path.dirname(__file__))

def _load_env_file(path, skip_keys=None):
    skip_keys = set(skip_keys or [])
    if not os.path.isfile(path):
        return
    with open(path) as env_file:
        for line in env_file:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                key = key.strip()
                if key in skip_keys:
                    continue
                os.environ[key] = val.strip()


# Automatically load environment files from the repository root.
def reload_runtime_env():
    _load_env_file(os.path.join(REPO_ROOT, ".env"))
    for file_name in GENERATED_ENV_FILES:
        _load_env_file(os.path.join(REPO_ROOT, file_name), skip_keys={"PIPELINE_ENV"})


reload_runtime_env()

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "your_openai_api_key")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", None)
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4")
OPENAI_MAX_TOKENS = int(os.environ.get("OPENAI_MAX_TOKENS", "4096"))
DEFAULT_PIPELINE_ENV = os.environ.get("PIPELINE_ENV", "gitea")
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
SFT_STEPWISE_PATH = os.path.join(MEMORY_DIR, "sft_dataset_stepwise.jsonl")
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
