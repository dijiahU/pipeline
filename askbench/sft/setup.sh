#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RESULTS_DIR="$(cd "$SCRIPT_DIR/../results" && pwd)"
CONDA_ENV_NAME="${ASKBENCH_CONDA_ENV:-askbench-qwen35}"
CONDA_ENV_PREFIX="${ASKBENCH_CONDA_PREFIX:-$ROOT_DIR/.conda-envs/$CONDA_ENV_NAME}"
CONDA_HOME="${CONDA_HOME:-$HOME/miniconda3}"
CONDA_SH="${CONDA_SH:-$CONDA_HOME/etc/profile.d/conda.sh}"
PYTHON_VERSION="${PYTHON_VERSION:-3.11}"
LLAMAFACTORY_REF="${LLAMAFACTORY_REF:-main}"
INSTALL_TORCH="${INSTALL_TORCH:-1}"
INSTALL_FLASH_ATTN="${INSTALL_FLASH_ATTN:-0}"
TORCH_INSTALL_CMD="${TORCH_INSTALL_CMD:-}"
TORCH_VERSION="${TORCH_VERSION:-2.10.0}"
TORCHVISION_VERSION="${TORCHVISION_VERSION:-0.25.0}"
TORCHAUDIO_VERSION="${TORCHAUDIO_VERSION:-2.10.0}"
TORCH_INDEX_URL="${TORCH_INDEX_URL:-https://download.pytorch.org/whl/cu128}"

die() {
    echo "ERROR: $*" >&2
    exit 1
}

load_conda() {
    if [ -f "$CONDA_SH" ]; then
        # shellcheck disable=SC1090
        source "$CONDA_SH"
    elif command -v conda >/dev/null 2>&1; then
        eval "$(conda shell.bash hook)"
    else
        die "Conda not found. Install Miniconda first, then rerun setup.sh. Suggested path: $CONDA_HOME"
    fi

    if ! command -v conda >/dev/null 2>&1; then
        die "Conda command is still unavailable after initialization. Check CONDA_HOME or CONDA_SH."
    fi

    eval "$(conda shell.bash hook)"
}

setup_conda_env() {
    mkdir -p "$(dirname "$CONDA_ENV_PREFIX")"

    if [ -x "$CONDA_ENV_PREFIX/bin/python" ]; then
        echo "[1/5] Reusing conda env prefix: $CONDA_ENV_PREFIX"
    else
        echo "[1/5] Creating conda env prefix: $CONDA_ENV_PREFIX (python=$PYTHON_VERSION)"
        conda create -y -p "$CONDA_ENV_PREFIX" "python=$PYTHON_VERSION" pip git
    fi

    conda activate "$CONDA_ENV_PREFIX"
    python -m pip install --upgrade pip setuptools wheel

    python - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit(
        "Python 3.11+ is required for current Qwen3.5 fine-tuning. "
        f"Detected: {sys.version.split()[0]}"
    )
print(f"Using Python {sys.version.split()[0]} in active conda env")
PY
}

install_project_deps() {
    echo "[2/5] Installing repository dependencies"
    python -m pip install -r "$ROOT_DIR/requirements.txt"
}

install_torch_stack() {
    if [ "$INSTALL_TORCH" != "1" ]; then
        echo "[3/5] Skipping torch install because INSTALL_TORCH=$INSTALL_TORCH"
        return
    fi

    echo "[3/5] Installing torch stack"
    if [ -n "$TORCH_INSTALL_CMD" ]; then
        echo "  Running custom TORCH_INSTALL_CMD"
        eval "$TORCH_INSTALL_CMD"
    else
        python -m pip install \
            "torch==$TORCH_VERSION" \
            "torchvision==$TORCHVISION_VERSION" \
            "torchaudio==$TORCHAUDIO_VERSION" \
            --index-url "$TORCH_INDEX_URL"
    fi
}

install_llamafactory() {
    echo "[4/5] Installing LLaMA-Factory from git ref: $LLAMAFACTORY_REF"
    python -m pip install "git+https://github.com/hiyouga/LLaMA-Factory.git@${LLAMAFACTORY_REF}"

    if [ "$INSTALL_FLASH_ATTN" = "1" ]; then
        echo "  Installing flash-attn"
        python -m pip install flash-attn --no-build-isolation
    fi
}

link_dataset() {
    echo "[5/5] Linking training data"
    ln -sfn ../results/sft_train.json "$SCRIPT_DIR/sft_train.json"

    python - <<PY
import json
from pathlib import Path
script_dir = Path(r"$SCRIPT_DIR")
dataset_path = script_dir / "sft_train.json"
info_path = script_dir / "dataset_info.json"
records = json.loads(dataset_path.read_text())
info = json.loads(info_path.read_text())
print(f"  Training records: {len(records)}")
print(f"  Registered datasets: {list(info.keys())}")
PY
}

print_next_steps() {
    cat <<EOF

=== AskBench SFT setup complete ===

Conda environment:
  source "$CONDA_SH"
  conda activate "$CONDA_ENV_PREFIX"

Environment check:
  python "$SCRIPT_DIR/check_env.py"

Current mainline training path:
  cd "$SCRIPT_DIR"
  DISABLE_VERSION_CHECK=1 llamafactory-cli train train_lora_gpu_explicit160.yaml

Optional lower-VRAM fallback:
  cd "$SCRIPT_DIR"
  DISABLE_VERSION_CHECK=1 llamafactory-cli train train_qlora_gpu.yaml

Merge the adapter:
  cd "$SCRIPT_DIR"
  DISABLE_VERSION_CHECK=1 llamafactory-cli export merge_lora_explicit160.yaml

Interactive sanity check:
  cd "$SCRIPT_DIR"
  DISABLE_VERSION_CHECK=1 llamafactory-cli chat inference_merged_explicit160.yaml

Important:
  1. Update model_name_or_path in the YAML files to your exact Qwen3.5 checkpoint path or HF repo id.
  2. This setup assumes Miniconda or Anaconda is already installed in a shared path.
  3. The default conda env prefix is "$CONDA_ENV_PREFIX".
  4. Default torch install uses $TORCH_INDEX_URL to match the current cluster's CUDA 12.8 driver.
  5. Legacy non-explicit160 bf16 train / merge / inference YAMLs were removed to avoid confusion.
  6. If your cluster needs a different torch wheel, set TORCH_INSTALL_CMD before running setup.sh.
EOF
}

echo "=== AskBench Qwen3.5 SFT Setup ==="
load_conda
setup_conda_env
install_project_deps
install_torch_stack
install_llamafactory
link_dataset
print_next_steps
