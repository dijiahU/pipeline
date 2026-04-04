#!/usr/bin/env python3
"""
SFT data format conversion tool.

llamafactorysft.json is the human-editable readable version
(tools is a JSON array and value is an object).
The train command generates a training file in strict LlamaFactory format.

Usage:
    python sft_format.py train              # readable -> LlamaFactory training format
    python sft_format.py train -o out.json  # specify output path
    python sft_format.py check              # validate the readable file
"""

import json
import sys
from pathlib import Path

SRC = Path(__file__).parent / "llamafactorysft.json"
DEFAULT_OUT = Path(__file__).parent / "llamafactorysft_train.json"


def to_train_format(samples: list) -> list:
    """Convert the readable format into strict LlamaFactory format: tools becomes a string and value becomes a string."""
    out = []
    for sample in samples:
        s = dict(sample)
        # tools: list/object -> compact JSON string
        if not isinstance(s.get("tools"), str):
            s["tools"] = json.dumps(s["tools"], ensure_ascii=False, separators=(",", ":"))
        # conversations: value object -> JSON string
        convs = []
        for turn in s.get("conversations", []):
            t = dict(turn)
            if t["from"] in ("function_call", "observation") and not isinstance(t["value"], str):
                t["value"] = json.dumps(t["value"], ensure_ascii=False)
            convs.append(t)
        s["conversations"] = convs
        out.append(s)
    return out


def format_train(samples: list) -> str:
    """LlamaFactory format: system/tools on separate lines, and each conversation turn on its own line."""
    parts = ["["]
    for i, sample in enumerate(samples):
        parts.append("  {")
        parts.append(f"    \"system\": {json.dumps(sample['system'], ensure_ascii=False)},")
        parts.append(f"    \"tools\": {json.dumps(sample['tools'], ensure_ascii=False)},")
        parts.append("    \"conversations\": [")
        convs = sample["conversations"]
        for j, turn in enumerate(convs):
            line = json.dumps(turn, ensure_ascii=False)
            comma = "," if j < len(convs) - 1 else ""
            parts.append(f"      {line}{comma}")
        parts.append("    ]")
        comma = "," if i < len(samples) - 1 else ""
        parts.append(f"  }}{comma}")
    parts.append("]")
    return "\n".join(parts) + "\n"


def check(samples: list):
    """Validate the samples."""
    errors = []
    for i, sample in enumerate(samples):
        if "system" not in sample:
            errors.append(f"Sample {i+1}: missing system field")
        if "tools" not in sample:
            errors.append(f"Sample {i+1}: missing tools field")
        if "conversations" not in sample:
            errors.append(f"Sample {i+1}: missing conversations field")
            continue
        convs = sample["conversations"]
        if not convs or convs[0].get("from") != "human":
            errors.append(f"Sample {i+1}: conversations must start with human")
        # Validate function_call value format.
        for j, turn in enumerate(convs):
            role = turn.get("from")
            val = turn.get("value")
            if role == "function_call":
                obj = val if isinstance(val, dict) else None
                if obj is None:
                    try:
                        obj = json.loads(val)
                    except (json.JSONDecodeError, TypeError):
                        pass
                if not isinstance(obj, dict) or "name" not in obj:
                    errors.append(f"Sample {i+1} turn {j+1}: function_call value is missing name")
            if role not in ("human", "gpt", "function_call", "observation"):
                errors.append(f"Sample {i+1} turn {j+1}: unknown role '{role}'")
    return errors


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("train", "check"):
        print(__doc__.strip())
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "train":
        out_path = DEFAULT_OUT
        if "-o" in sys.argv:
            idx = sys.argv.index("-o")
            if idx + 1 < len(sys.argv):
                out_path = Path(sys.argv[idx + 1])

        data = json.loads(SRC.read_text("utf-8"))
        result = to_train_format(data)
        out_path.write_text(format_train(result), "utf-8")
        n = len(result)
        total_turns = sum(len(s["conversations"]) for s in result)
        print(f"train: {SRC.name} -> {out_path.name}  ({n} samples, {total_turns} turns)")

    elif cmd == "check":
        data = json.loads(SRC.read_text("utf-8"))
        errs = check(data)
        n = len(data)
        total_turns = sum(len(s.get("conversations", [])) for s in data)
        if errs:
            print(f"Found {len(errs)} issues:")
            for e in errs:
                print(f"  - {e}")
            sys.exit(1)
        else:
            print(f"check: {SRC.name} is valid ({n} samples, {total_turns} turns)")


if __name__ == "__main__":
    main()
