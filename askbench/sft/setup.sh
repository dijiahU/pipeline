#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RESULTS_DIR="$(cd "$SCRIPT_DIR/../results" && pwd)"

echo "=== AskBench SFT Setup ==="

# 1. Install LLaMA-Factory
echo "[1/3] Installing LLaMA-Factory..."
if command -v llamafactory-cli &>/dev/null; then
    echo "  llamafactory-cli already installed, skipping."
else
    pip install llamafactory
    echo "  Done."
fi

# 2. Symlink training data into sft/ directory (LLaMA-Factory reads from dataset_dir)
echo "[2/3] Linking training data..."
if [ -f "$SCRIPT_DIR/sft_train.json" ]; then
    echo "  sft_train.json already exists in sft/, skipping."
else
    ln -sf "$RESULTS_DIR/sft_train.json" "$SCRIPT_DIR/sft_train.json"
    echo "  Linked: results/sft_train.json -> sft/sft_train.json"
fi

# 3. Verify
echo "[3/3] Verifying..."
RECORD_COUNT=$(python3 -c "import json; print(len(json.load(open('$SCRIPT_DIR/sft_train.json'))))")
echo "  Training records: $RECORD_COUNT"
echo "  dataset_info.json: $(cat "$SCRIPT_DIR/dataset_info.json" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(list(d.keys()))')"

echo ""
echo "=== Setup complete ==="
echo ""
echo "To train on Mac:"
echo "  cd $SCRIPT_DIR"
echo "  PYTORCH_ENABLE_MPS_FALLBACK=1 llamafactory-cli train train_lora_mac.yaml"
echo ""
echo "To train on GPU cluster:"
echo "  cd $SCRIPT_DIR"
echo "  llamafactory-cli train train_lora_gpu.yaml"
echo ""
echo "After training, merge LoRA adapter:"
echo "  llamafactory-cli export merge_lora.yaml"
echo ""
echo "Test with interactive chat:"
echo "  llamafactory-cli chat inference.yaml"
