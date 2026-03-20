#!/usr/bin/env python3
"""
SFT 数据格式转换工具。

llamafactorysft.json 是人工编辑的可读版本（tools 是 JSON 数组，value 是对象）。
train 命令生成 LlamaFactory 严格格式的训练文件。

用法:
    python sft_format.py train              # 可读 -> LlamaFactory 训练格式
    python sft_format.py train -o out.json  # 指定输出路径
    python sft_format.py check              # 检查可读文件的合法性
"""

import json
import sys
from pathlib import Path

SRC = Path(__file__).parent / "llamafactorysft.json"
DEFAULT_OUT = Path(__file__).parent / "llamafactorysft_train.json"


def to_train_format(samples: list) -> list:
    """把可读格式转成 LlamaFactory 严格格式：tools 变字符串，value 变字符串。"""
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
    """LlamaFactory 格式：system/tools 各一行，conversations 每条一行。"""
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
    """检查样本合法性。"""
    errors = []
    for i, sample in enumerate(samples):
        if "system" not in sample:
            errors.append(f"样本 {i+1}: 缺少 system 字段")
        if "tools" not in sample:
            errors.append(f"样本 {i+1}: 缺少 tools 字段")
        if "conversations" not in sample:
            errors.append(f"样本 {i+1}: 缺少 conversations 字段")
            continue
        convs = sample["conversations"]
        if not convs or convs[0].get("from") != "human":
            errors.append(f"样本 {i+1}: conversations 必须以 human 开头")
        # 检查 function_call value 格式
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
                    errors.append(f"样本 {i+1} 轮 {j+1}: function_call value 缺少 name")
            if role not in ("human", "gpt", "function_call", "observation"):
                errors.append(f"样本 {i+1} 轮 {j+1}: 未知角色 '{role}'")
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
        print(f"train: {SRC.name} -> {out_path.name}  ({n} 样本, {total_turns} 轮)")

    elif cmd == "check":
        data = json.loads(SRC.read_text("utf-8"))
        errs = check(data)
        n = len(data)
        total_turns = sum(len(s.get("conversations", [])) for s in data)
        if errs:
            print(f"发现 {len(errs)} 个问题:")
            for e in errs:
                print(f"  - {e}")
            sys.exit(1)
        else:
            print(f"check: {SRC.name} 合法 ({n} 样本, {total_turns} 轮)")


if __name__ == "__main__":
    main()
