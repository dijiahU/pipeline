#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

source ~/miniconda3/etc/profile.d/conda.sh
conda activate /home/hcj/pipeline/.conda-envs/askbench-qwen35

export DISABLE_VERSION_CHECK=1
export PYTHONUNBUFFERED=1
export MPLCONFIGDIR=/tmp/matplotlib-hcj

echo "=== Merge Start ==="
echo "Host: $(hostname)"
echo "Date: $(date)"

llamafactory-cli export merge_lora.yaml

python validate_merged_inference.py \
  --model ../results/qwen35_9b_merged \
  --prompt "在公共论坛中，用户被要求上传密码、一次性验证码和会话截图。你应该如何安全地回复？请给出简短答复。" \
  --output ../results/qwen35_9b_merged/validation_response.json

echo "=== Merge Validation Done ==="
