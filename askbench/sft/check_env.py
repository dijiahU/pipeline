"""Minimal hardware and dependency check for the TRL decision-token SFT path."""

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
    print("=== AskBench TRL Decision-Token SFT Environment Check ===")
    print(f"Python: {platform.python_version()}")
    print(f"Platform: {platform.platform()}")

    transformers = _safe_import("transformers")
    trl = _safe_import("trl")
    peft = _safe_import("peft")
    datasets = _safe_import("datasets")
    print(f"transformers: {getattr(transformers, '__version__', 'missing') if transformers else 'missing'}")
    print(f"trl: {getattr(trl, '__version__', 'missing') if trl else 'missing'}")
    print(f"peft: {getattr(peft, '__version__', 'missing') if peft else 'missing'}")
    print(f"datasets: {getattr(datasets, '__version__', 'missing') if datasets else 'missing'}")

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
        if best_gpu_mem >= 16:
            print("Recommended profile: train_trl_decision_tokens.yaml")
            print("Why: enough VRAM for the current TRL + LoRA decision-token path on a smaller instruct model.")
        else:
            print("Recommended profile: none")
            print("Why: GPUs under 16 GB are likely too tight for practical fine-tuning.")

        return 0

    if mps_available:
        print("MPS: available")
        print("Recommendation: use this machine only for smoke tests, setup, or preprocessing.")
        return 0

    print("CUDA: unavailable")
    nvidia_smi = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"])
    if nvidia_smi:
        print("nvidia-smi:")
        print(nvidia_smi)
    else:
        print("nvidia-smi: not found")

    print("Recommendation: this machine is suitable only for setup / preprocessing, not GPU fine-tuning.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
