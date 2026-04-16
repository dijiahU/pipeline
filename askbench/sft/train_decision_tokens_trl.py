#!/usr/bin/env python3
"""TRL-based LoRA SFT entrypoint for the decision-token dataset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml
from datasets import Dataset
from peft import LoraConfig
from transformers import AutoModelForCausalLM, AutoTokenizer, set_seed
from trl import SFTConfig, SFTTrainer


DEFAULT_SPECIAL_TOKENS = [
    "<|direct_execute|>",
    "<|ask_human|>",
    "<|refuse|>",
    "<|replan|>",
]

DEFAULT_MODULES_TO_SAVE = [
    "embed_tokens",
    "lm_head",
]


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"Config file must decode to an object: {path}")
    return data


def _resolve_path(path_value: str | None, base_dir: Path) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _load_dataset(dataset_path: Path) -> Dataset:
    records = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise RuntimeError(f"Dataset must be a JSON list: {dataset_path}")
    if not records:
        raise RuntimeError(f"Dataset is empty: {dataset_path}")

    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise RuntimeError(f"Dataset record #{index} is not an object.")
        prompt = record.get("prompt")
        completion = record.get("completion")
        if not isinstance(prompt, list) or not prompt:
            raise RuntimeError(f"Dataset record #{index} is missing a non-empty prompt list.")
        if not isinstance(completion, list) or not completion:
            raise RuntimeError(f"Dataset record #{index} is missing a non-empty completion list.")

    return Dataset.from_list(records)


def _normalize_module_setting(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if "," in stripped:
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return stripped
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return value


def _load_model_dtype(value: str | None):
    if not value or value == "auto":
        return "auto"

    import torch

    mapping = {
        "bfloat16": torch.bfloat16,
        "float16": torch.float16,
        "float32": torch.float32,
    }
    if value not in mapping:
        raise RuntimeError(f"Unsupported torch_dtype: {value}")
    return mapping[value]


def _ensure_special_tokens(tokenizer, model, special_tokens):
    tokens = [str(token).strip() for token in (special_tokens or []) if str(token).strip()]
    if not tokens:
        return

    added = tokenizer.add_special_tokens({"additional_special_tokens": tokens})
    if tokenizer.pad_token is None:
        if tokenizer.eos_token is not None:
            tokenizer.pad_token = tokenizer.eos_token
        else:
            tokenizer.add_special_tokens({"pad_token": "<|pad|>"})
            added += 1

    if added > 0:
        model.resize_token_embeddings(len(tokenizer))


def _build_lora_config(config: dict[str, Any]) -> LoraConfig:
    return LoraConfig(
        r=int(config.get("lora_rank", 32)),
        lora_alpha=int(config.get("lora_alpha", 64)),
        lora_dropout=float(config.get("lora_dropout", 0.05)),
        target_modules=_normalize_module_setting(config.get("lora_target_modules", "all-linear")),
        modules_to_save=_normalize_module_setting(config.get("modules_to_save", DEFAULT_MODULES_TO_SAVE)),
        bias=str(config.get("lora_bias", "none")),
        task_type="CAUSAL_LM",
    )


def _build_sft_config(config: dict[str, Any], output_dir: Path) -> SFTConfig:
    kwargs = {
        "output_dir": str(output_dir),
        "max_length": config.get("max_length", 4096),
        "packing": bool(config.get("packing", False)),
        "learning_rate": float(config.get("learning_rate", 2.0e-4)),
        "num_train_epochs": float(config.get("num_train_epochs", 5.0)),
        "lr_scheduler_type": str(config.get("lr_scheduler_type", "cosine")),
        "per_device_train_batch_size": int(config.get("per_device_train_batch_size", 1)),
        "per_device_eval_batch_size": int(config.get("per_device_eval_batch_size", 1)),
        "gradient_accumulation_steps": int(config.get("gradient_accumulation_steps", 16)),
        "warmup_ratio": float(config.get("warmup_ratio", 0.05)),
        "optim": str(config.get("optim", "adamw_torch")),
        "logging_steps": int(config.get("logging_steps", 1)),
        "save_steps": int(config.get("save_steps", 50)),
        "save_total_limit": int(config.get("save_total_limit", 3)),
        "report_to": config.get("report_to", "tensorboard"),
        "bf16": config.get("bf16", True),
        "fp16": config.get("fp16", False),
        "gradient_checkpointing": bool(config.get("gradient_checkpointing", True)),
        "dataset_num_proc": config.get("dataset_num_proc", 16),
        "dataloader_num_workers": int(config.get("dataloader_num_workers", 4)),
        "completion_only_loss": config.get("completion_only_loss", True),
        "assistant_only_loss": config.get("assistant_only_loss", False),
        "save_only_model": bool(config.get("save_only_model", False)),
        "overwrite_output_dir": bool(config.get("overwrite_output_dir", True)),
        "ddp_timeout": int(config.get("ddp_timeout", 180000000)),
        "seed": int(config.get("seed", 42)),
        "chat_template_path": config.get("chat_template_path"),
        "remove_unused_columns": bool(config.get("remove_unused_columns", True)),
    }
    return SFTConfig(**kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description="Train the decision-token safety model with TRL.")
    parser.add_argument("--config", default="train_trl_decision_tokens.yaml", help="Path to a YAML config file.")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    config_dir = config_path.parent
    config = _load_yaml(config_path)

    model_name_or_path = str(config.get("model_name_or_path", "")).strip()
    if not model_name_or_path or "/path/to/your/" in model_name_or_path:
        raise RuntimeError("model_name_or_path is still unset in the TRL config.")

    dataset_path = _resolve_path(config.get("dataset_path", "pipeline_decision_token_sft.json"), config_dir)
    output_dir = _resolve_path(config.get("output_dir", "../results/decision_token_adapter_trl"), config_dir)
    if dataset_path is None or output_dir is None:
        raise RuntimeError("dataset_path and output_dir must be set.")

    set_seed(int(config.get("seed", 42)))
    dataset = _load_dataset(dataset_path)

    trust_remote_code = bool(config.get("trust_remote_code", True))
    tokenizer = AutoTokenizer.from_pretrained(model_name_or_path, trust_remote_code=trust_remote_code)
    if not tokenizer.chat_template and not config.get("chat_template_path"):
        raise RuntimeError(
            "The selected tokenizer has no chat_template. "
            "Use an instruct model with a chat template, or set chat_template_path in the config."
        )

    model = AutoModelForCausalLM.from_pretrained(
        model_name_or_path,
        trust_remote_code=trust_remote_code,
        torch_dtype=_load_model_dtype(config.get("torch_dtype", "auto")),
    )
    if bool(config.get("gradient_checkpointing", True)):
        model.config.use_cache = False

    _ensure_special_tokens(tokenizer, model, config.get("special_tokens", DEFAULT_SPECIAL_TOKENS))

    sft_config = _build_sft_config(config, output_dir)
    lora_config = _build_lora_config(config)
    tie_word_embeddings = getattr(getattr(model, "config", None), "tie_word_embeddings", None)

    print("=== TRL Decision-Token Training ===")
    print(f"Config: {config_path}")
    print(f"Dataset: {dataset_path}")
    print(f"Samples: {len(dataset)}")
    print(f"Output dir: {output_dir}")
    print(f"Model: {model_name_or_path}")
    print(f"Completion-only loss: {sft_config.completion_only_loss}")
    print(f"Model tie_word_embeddings: {tie_word_embeddings}")
    print(f"LoRA modules_to_save: {lora_config.modules_to_save}")
    print(
        "Embedding/output fallback: training keeps embed_tokens + lm_head trainable "
        "because the chosen Qwen3.5-family checkpoint may be tied or untied; "
        "we have not finalized that assumption in this repo yet."
    )

    trainer = SFTTrainer(
        model=model,
        args=sft_config,
        train_dataset=dataset,
        processing_class=tokenizer,
        peft_config=lora_config,
    )

    train_result = trainer.train(resume_from_checkpoint=config.get("resume_from_checkpoint"))
    trainer.save_model()
    tokenizer.save_pretrained(output_dir)
    trainer.log_metrics("train", train_result.metrics)
    trainer.save_metrics("train", train_result.metrics)
    trainer.save_state()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
