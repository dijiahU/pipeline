#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import torch
from transformers import AutoProcessor, Qwen3_5ForConditionalGeneration


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one text-only validation on a merged Qwen3.5 model.")
    parser.add_argument("--model", required=True, help="Path to the merged model directory.")
    parser.add_argument("--prompt", required=True, help="Validation prompt.")
    parser.add_argument("--output", required=True, help="Path to save the validation result JSON.")
    parser.add_argument("--max-new-tokens", type=int, default=256)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    model_path = Path(args.model).resolve()
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
    device_map = "auto" if torch.cuda.is_available() else None

    processor = AutoProcessor.from_pretrained(model_path, trust_remote_code=True)
    model = Qwen3_5ForConditionalGeneration.from_pretrained(
        model_path,
        trust_remote_code=True,
        torch_dtype=dtype,
        device_map=device_map,
    )

    messages = [
        {
            "role": "system",
            "content": [{"type": "text", "text": "You are a careful assistant. Give a short direct answer."}],
        },
        {
            "role": "user",
            "content": [{"type": "text", "text": args.prompt}],
        },
    ]

    inputs = processor.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=True,
        return_dict=True,
        return_tensors="pt",
    )
    if torch.cuda.is_available():
        inputs = {key: value.to(model.device) for key, value in inputs.items()}

    with torch.inference_mode():
        generated_ids = model.generate(
            **inputs,
            do_sample=False,
            max_new_tokens=args.max_new_tokens,
        )

    generated_ids_trimmed = [
        out_ids[len(in_ids) :] for in_ids, out_ids in zip(inputs["input_ids"], generated_ids)
    ]
    output_text = processor.batch_decode(
        generated_ids_trimmed,
        skip_special_tokens=True,
        clean_up_tokenization_spaces=False,
    )[0]

    result = {
        "model": str(model_path),
        "prompt": args.prompt,
        "response": output_text,
    }
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
