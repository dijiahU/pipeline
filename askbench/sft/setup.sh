#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
CONDA_ENV_NAME="${ASKBENCH_CONDA_ENV:-askbench-decision-sft}"
CONDA_ENV_PREFIX="${ASKBENCH_CONDA_PREFIX:-$ROOT_DIR/.conda-envs/$CONDA_ENV_NAME}"
CONDA_HOME="${CONDA_HOME:-$HOME/miniconda3}"
CONDA_SH="${CONDA_SH:-$CONDA_HOME/etc/profile.d/conda.sh}"
PYTHON_VERSION="${PYTHON_VERSION:-3.11}"
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
        "Python 3.11+ is required for the current decision-token fine-tuning path. "
        f"Detected: {sys.version.split()[0]}"
    )
print(f"Using Python {sys.version.split()[0]} in active conda env")
PY
}

install_project_deps() {
    echo "[2/5] Installing repository dependencies"
    python -m pip install -r "$ROOT_DIR/requirements.txt"
    python -m pip install -r "$ROOT_DIR/askbench/requirements.txt"
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

install_training_stack() {
    echo "[4/5] Verifying TRL training stack"
    python - <<'PY'
import datasets
import peft
import transformers
import trl
print(f"transformers={transformers.__version__}")
print(f"trl={trl.__version__}")
print(f"peft={peft.__version__}")
print(f"datasets={datasets.__version__}")
PY
    if [ "$INSTALL_FLASH_ATTN" = "1" ]; then
        echo "  Installing flash-attn"
        python -m pip install flash-attn --no-build-isolation
    fi
}

link_dataset() {
    echo "[5/5] Linking training data"
    if [ -f "$ROOT_DIR/artifacts/decision_token_sft.json" ]; then
        ln -sfn ../../artifacts/decision_token_sft.json "$SCRIPT_DIR/pipeline_decision_token_sft.json"
    else
        printf '[]\n' > "$SCRIPT_DIR/pipeline_decision_token_sft.json"
    fi

    python - <<PY
import json
from pathlib import Path
script_dir = Path(r"$SCRIPT_DIR")
decision_dataset_path = script_dir / "pipeline_decision_token_sft.json"
decision_records = json.loads(decision_dataset_path.read_text())
print(f"  Decision-token records: {len(decision_records)}")
print(f"  Record keys: {list(decision_records[0].keys()) if decision_records else []}")
PY
}

print_next_steps() {
    cat <<EOF

=== AskBench TRL SFT setup complete ===

Conda environment:
  source "$CONDA_SH"
  conda activate "$CONDA_ENV_PREFIX"

Environment check:
  python "$SCRIPT_DIR/check_env.py"

Current mainline training path:
  cd "$SCRIPT_DIR"
  python train_decision_tokens_trl.py --config train_trl_decision_tokens.yaml

Important:
  1. Update model_name_or_path in train_trl_decision_tokens.yaml to your actual base-model path or HF repo id.
  2. Default output_dir is ../results/decision_token_adapter_trl and will be created by the TRL training script.
  3. This setup assumes Miniconda or Anaconda is already installed in a shared path.
  4. The default conda env prefix is "$CONDA_ENV_PREFIX".
  5. Default torch install uses $TORCH_INDEX_URL to match the current cluster's CUDA 12.8 driver.
  6. If your cluster needs a different torch wheel, set TORCH_INSTALL_CMD before running setup.sh.
EOF
}

echo "=== AskBench TRL Decision-Token SFT Setup ==="
load_conda
setup_conda_env
install_project_deps
install_torch_stack
install_training_stack
link_dataset
print_next_steps
