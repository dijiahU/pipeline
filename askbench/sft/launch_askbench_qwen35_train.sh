#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
TRAIN_CONFIG="${ASKBENCH_TRAIN_CONFIG:-train_lora_gpu_explicit160.yaml}"

source ~/miniconda3/etc/profile.d/conda.sh
conda activate /home/hcj/pipeline/.conda-envs/askbench-qwen35

if [ ! -f "$TRAIN_CONFIG" ]; then
    echo "ERROR: training config not found: $TRAIN_CONFIG"
    exit 1
fi

if grep -q "/path/to/your/" "$TRAIN_CONFIG"; then
    echo "ERROR: $TRAIN_CONFIG still contains the placeholder model_name_or_path."
    echo "Please replace it with your actual Qwen3.5 checkpoint path or HF repo id before submission."
    exit 1
fi

# This local Slurm deployment does not fully hide non-allocated GPUs, so bind manually.
if [ -n "${SLURM_STEP_GPUS:-}" ]; then
    export CUDA_VISIBLE_DEVICES="$SLURM_STEP_GPUS"
elif [ -n "${SLURM_JOB_GPUS:-}" ]; then
    export CUDA_VISIBLE_DEVICES="$SLURM_JOB_GPUS"
elif [ -n "${SLURM_JOB_ID:-}" ]; then
    GPU_IDX="$(scontrol show job -d "$SLURM_JOB_ID" | sed -n 's/.*GRES=gpu:[0-9][0-9]*(IDX:\([^)]*\)).*/\1/p' | head -n 1)"
    if [ -n "$GPU_IDX" ]; then
        export CUDA_VISIBLE_DEVICES="$GPU_IDX"
    fi
fi

echo "=== Job Start ==="
echo "Host: $(hostname)"
echo "Workdir: $SCRIPT_DIR"
echo "Date: $(date)"
echo "Train config: $TRAIN_CONFIG"
echo "CUDA_VISIBLE_DEVICES: ${CUDA_VISIBLE_DEVICES:-<unset>}"

python check_env.py

export DISABLE_VERSION_CHECK=1
export PYTHONUNBUFFERED=1

llamafactory-cli train "$TRAIN_CONFIG"
