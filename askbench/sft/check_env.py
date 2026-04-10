"""Minimal hardware check for AskBench SFT profiles."""

from __future__ import annotations

import importlib
import platform
import subprocess


def _safe_import(name: str):
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True).strip()
    except Exception:
        return ""


def _format_gb(value: float) -> str:
    return f"{value:.1f} GB"


def main() -> int:
    print("=== AskBench SFT Environment Check ===")
    print(f"Python: {platform.python_version()}")
    print(f"Platform: {platform.platform()}")

    torch = _safe_import("torch")
    if torch is None:
        print("torch: missing")
        print("Recommendation: finish setup.sh before selecting a training profile.")
        return 0

    print(f"torch: {getattr(torch, '__version__', 'unknown')}")

    cuda_available = bool(torch.cuda.is_available())
    mps_available = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())

    if cuda_available:
        print("CUDA: available")
        device_count = torch.cuda.device_count()
        total_memories = []
        for idx in range(device_count):
            props = torch.cuda.get_device_properties(idx)
            mem_gb = props.total_memory / (1024 ** 3)
            total_memories.append(mem_gb)
            print(f"GPU {idx}: {props.name} ({_format_gb(mem_gb)})")

        best_gpu_mem = max(total_memories)
        if best_gpu_mem >= 48:
            print("Recommended profile: train_lora_gpu_explicit160.yaml")
            print("Why: enough VRAM for bf16 LoRA with the longer AskBench context.")
        elif best_gpu_mem >= 24:
            print("Recommended profile: train_qlora_gpu.yaml")
            print("Why: 4-bit QLoRA is the fallback path when the explicit160 bf16 profile does not fit.")
            print("If you hit OOM, lower cutoff_len from 6144 to 4096 first.")
        else:
            print("Recommended profile: none")
            print("Why: GPUs under 24 GB are likely too tight for Qwen3.5-9B AskBench SFT.")

        return 0

    if mps_available:
        print("MPS: available")
        print("Recommendation: use train_lora_mac.yaml only as a small-model smoke test.")
        print("Qwen3.5-9B training is not recommended on MPS for this dataset.")
        return 0

    print("CUDA: unavailable")
    nvidia_smi = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"])
    if nvidia_smi:
        print("nvidia-smi:")
        print(nvidia_smi)
    else:
        print("nvidia-smi: not found")

    print("Recommendation: this machine is suitable only for setup / preprocessing, not Qwen3.5-9B training.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
