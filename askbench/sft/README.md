# AskBench Qwen3.5 SFT

This directory now contains a practical training path for the AskBench ShareGPT dataset in
`sft_train.json`.

## What changed

- `setup.sh` now bootstraps a dedicated conda environment, installs repository dependencies,
  installs `LLaMA-Factory` from GitHub `main`, and links the dataset.
- `train_lora_gpu_explicit160.yaml` is the only bf16 LoRA mainline kept in this repo.
- The legacy non-`explicit160` train / merge / inference YAMLs were removed to avoid mixing runs.
- `train_qlora_gpu.yaml` is kept only as a lower-VRAM fallback profile.
- The current Qwen3.5 path uses the `qwen3_5` template, so future training and inference run in
  thinking mode by default.
- `train_lora_mac.yaml` is now explicitly a small-model smoke test only.
- `check_env.py` prints a profile recommendation based on the available hardware.

## Why the cutoff length changed

The original `cutoff_len: 2048` is too short for AskBench. The dataset examples include long tool
schemas, system prompts, and two tool-call turns. In this repo the raw per-example payload is
roughly 17k to 23k characters, so the GPU profiles now use longer sequence lengths.

## Preconditions

- Install Miniconda or Anaconda first. The cluster tutorial in
  [ASPIRE_slurm_tutorial.md](/home/hcj/pipeline/askbench/ASPIRE_slurm_tutorial.md#L43) uses
  `~/miniconda3`.
- The conda environment created by `setup.sh` uses Python 3.11 by default.
- `setup.sh` now installs the CUDA 12.8 PyTorch wheels by default because the current compute
  nodes expose CUDA 12.8 drivers.
- For Qwen3.5-9B training, use a CUDA GPU.
- Before training, update `model_name_or_path` in the YAML files to the exact Qwen3.5 checkpoint
  path or Hugging Face repo id available in your environment.

## One-time setup

```bash
cd /home/hcj/pipeline/askbench/sft
source ~/miniconda3/etc/profile.d/conda.sh
bash setup.sh
conda activate /home/hcj/pipeline/.conda-envs/askbench-qwen35
python check_env.py
```

By default the environment is created inside the repo at
`/home/hcj/pipeline/.conda-envs/askbench-qwen35`. This avoids relying on global writable conda
directories. If you want a different prefix or Miniconda location, point the script at them
explicitly:

```bash
export CONDA_HOME=/path/to/miniconda3
export ASKBENCH_CONDA_PREFIX=/shared/path/askbench-qwen35
bash setup.sh
```

If your cluster needs a specific PyTorch wheel, set `TORCH_INSTALL_CMD` before running setup:

```bash
export TORCH_INSTALL_CMD="python -m pip install torch==2.10.0 torchvision==0.25.0 torchaudio==2.10.0 --index-url https://download.pytorch.org/whl/cu128"
bash setup.sh
```

## Train

Current mainline path:

```bash
cd /home/hcj/pipeline/askbench/sft
source ~/miniconda3/etc/profile.d/conda.sh
conda activate /home/hcj/pipeline/.conda-envs/askbench-qwen35
DISABLE_VERSION_CHECK=1 llamafactory-cli train train_lora_gpu_explicit160.yaml
```

Optional lower-VRAM fallback:

```bash
cd /home/hcj/pipeline/askbench/sft
source ~/miniconda3/etc/profile.d/conda.sh
conda activate /home/hcj/pipeline/.conda-envs/askbench-qwen35
DISABLE_VERSION_CHECK=1 llamafactory-cli train train_qlora_gpu.yaml
```

Mac smoke test only:

```bash
cd /home/hcj/pipeline/askbench/sft
source ~/miniconda3/etc/profile.d/conda.sh
conda activate /home/hcj/pipeline/.conda-envs/askbench-qwen35
PYTORCH_ENABLE_MPS_FALLBACK=1 DISABLE_VERSION_CHECK=1 llamafactory-cli train train_lora_mac.yaml
```

## Merge and sanity check

```bash
cd /home/hcj/pipeline/askbench/sft
source ~/miniconda3/etc/profile.d/conda.sh
conda activate /home/hcj/pipeline/.conda-envs/askbench-qwen35
DISABLE_VERSION_CHECK=1 llamafactory-cli export merge_lora_explicit160.yaml
DISABLE_VERSION_CHECK=1 llamafactory-cli chat inference_merged_explicit160.yaml
```

## Notes

- `train_lora_gpu_explicit160.yaml` is the default training entrypoint.
- `train_qlora_gpu.yaml` is only a fallback when bf16 LoRA does not fit.
- `run_askbench_qwen35_train.slurm` now defaults to `train_lora_gpu_explicit160.yaml`.
- The bf16 LoRA profile uses `cutoff_len: 4096` to fit more safely on a single A40 48GB.
- The current Qwen3.5 training / merge / inference YAMLs all use `template: qwen3_5`.
- If QLoRA runs out of memory, lower `cutoff_len` from `6144` to `4096` before reducing LoRA rank.
- In `run.slurm`, explicitly source `conda.sh` and `conda activate /home/hcj/pipeline/.conda-envs/askbench-qwen35`
  before running `llamafactory-cli`.
- If `torch.cuda.is_available()` is `False` on a compute node and the warning mentions an old
  NVIDIA driver, reinstall the `cu128` PyTorch wheel set.
- If you need benchmark serving after training, serve the merged checkpoint behind an OpenAI-style
  endpoint and point `askbench/config.py` at that endpoint.
