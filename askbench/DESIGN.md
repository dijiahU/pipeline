# AskBench: 独立 ask_human 能力测试框架

## Context

目标是**单独测试模型在应该调用 ask_human 时是否能正确调用**的能力。当前 safety_pipeline 的完整流程包含 predict_risk → memory → tool_try → judge → commit 等大量与本实验无关的逻辑。需要构建一个精简的独立测试框架 `askbench/`，保留 `predict_risk` 作为结构化风险理由层，并提供 `ask_human` / `refuse` 两个决策 flow tool，去除 memory 系统、try/commit/rollback 等运行时逻辑。

**实验设计**: 6 个实验 = 3 模型 × 2 prompt 策略
- 模型: GPT-5.4 / Qwen base / Qwen SFT
- Prompt: A (仅提供工具，不解释规则) / B (显式写明 ask_human 使用条件)
- 训练数据: 当前从 217 个 ask_human 任务中分层划分得到 181 个 train 任务，用 GPT-5.4 `explicit_rules` teacher 产出 160 条可蒸馏轨迹 → SFT 训练 Qwen

**简化流程**: 模型只需完成最多 2 轮 tool call 即停止:
1. Turn 1: 必须先调用 `predict_risk`
2. Turn 2: 根据自己的 risk judgment，自由选择 `ask_human` / `refuse` / 真实工具

---

## 实际执行结果

| 阶段 | 结果 |
|------|------|
| 任务总量 | 217 个 ask_human 任务，9 个服务 |
| Train/Test 划分 | 181 / 36（按服务 × new/legacy 分层抽样） |
| GPT-5.4 teacher 结果 | `explicit_rules` 在 train split 上产出 160 条 `asked_after_risky` 可用轨迹 |
| SFT 数据导出 | 160 条 |
| SFT 数据格式 | LLaMA-Factory ShareGPT JSON |
| 本地训练测试 | Qwen2.5-0.5B-Instruct + LoRA，Mac MPS 跑通 |

### 失败分析

当前保留为 teacher 的轨迹只来自 `gpt54_explicit_rules.jsonl` 中满足以下条件的记录：
- `final_action == ask_human`
- `decision_consistent is True`
- `result == asked_after_risky`

### 数据质量

- 当前最终 teacher 文件里，`predict_risk.tool` 为 flow tool 的脏轨迹已经清理完毕；导出的 160 条全部通过该校验

---

## 文件结构

```
askbench/
├── config.py                # API 配置、模型端点、路径常量
├── prompts.py               # 2 套 system prompt (bare / explicit_rules)
├── schemas.py               # flow tool schema 定义 + 加载各服务 real tool schema
├── tasks.py                 # 加载 task YAML，train/test split
├── llm.py                   # OpenAI 兼容 API 调用 (tool_choice=required)
├── runner.py                # 单任务执行: 构建 prompt → 调 LLM → 模拟 2-turn flow → 记录轨迹
├── gen_traces.py            # 批量用 GPT-5.4 生成 165 个任务的正确轨迹
├── export_sft.py            # 将轨迹转换为 LLaMA-Factory ShareGPT 格式
├── benchmark.py             # 运行 6 组实验 + 输出评分报告
├── evaluate.py              # 评分: accuracy / risk_detection_rate / ask_rate / error_rate
├── extract_schemas.py       # 一次性脚本: 从现有 *_tools.py 导出各服务 real tool schema
├── tool_schemas/            # 预导出的各服务 real tool schema (JSON)
│   ├── gitea.json (41)      ├── nocodb.json (17)
│   ├── mailu.json (30)      ├── erpnext.json (35)
│   ├── discourse.json (28)  ├── openemr.json (28)
│   ├── owncloud.json (20)   ├── rocketchat.json (42)
│   └── zammad.json (28)
├── sft/                     # LLaMA-Factory 训练配置
│   ├── dataset_info.json    # 数据集注册 (sharegpt 格式)
│   ├── train_lora_mac.yaml  # Mac MPS 训练配置
│   ├── train_qlora_gpu.yaml # 低显存回退配置
│   ├── train_lora_gpu_explicit160.yaml  # GPU 集群训练配置
│   ├── merge_lora_explicit160.yaml      # LoRA adapter 合并
│   ├── inference_explicit160.yaml       # 推理测试
│   ├── inference_merged_explicit160.yaml # merged 模型推理测试
│   ├── setup.sh             # 安装 LLaMA-Factory + 软链数据
│   └── sft_train.json       # → ../results/sft_train.json 软链
└── results/
    ├── gpt54_explicit_rules.jsonl # train split teacher 结果 (181 条，其中 160 条可蒸馏)
    └── sft_train.json        # LLaMA-Factory 格式的训练数据 (160 条)
```

---

## 模型看到的完整 Context

以 discourse 服务的一个任务为例，模型在每一轮看到：

### Turn 1

| 组件 | 内容 |
|------|------|
| System Prompt | explicit_rules 全文（安全判断规则） |
| User Message | `{"user_task": "...", "flow_phase": "need_risk", "service_context": {...}}` |
| Tools 列表 | flow tools (`predict_risk`, `ask_human`, `refuse`) + 当前任务相关 real tools |
| tool_choice | `"required"` |

### Turn 2

| 组件 | 内容 |
|------|------|
| Messages | Turn 1 全部上下文 + predict_risk 结果 + 更新后的 snapshot |
| Tools 列表 | 与当前任务相关的完整动作工具集（`ask_human` / `refuse` / real tools） |
| Snapshot 变化 | `flow_phase → "need_action"`, 新增 `current_risk_assessment` |

**不包含**: memory 系统、历史经验、plan、try/commit 机制、conversation context。纯测模型即时判断能力。

---

## 实现细节

### Step 1: `extract_schemas.py` — 导出 real tool schema

从现有 9 个 `*_tools.py` 中提取 `get_all_schemas()` 的输出，保存为 `tool_schemas/<service>.json`。这样 askbench 完全独立，不依赖 safety_pipeline 运行时。

### Step 2: `config.py` — 配置

- API 配置从 `.env` 自动加载（`.env` 优先覆盖 shell 环境变量）
- 三个模型配置：gpt54 / qwen_base / qwen_sft（通过环境变量切换端点）
- 关键常量：TRAIN_COUNT=185, SPLIT_SEED=42, MAX_LLM_RETRIES=2, MAX_TOKENS=1024

### Step 3: `schemas.py` — 工具 schema

- **Flow tool schemas**: `predict_risk` (tool, tool_args, description, result, reasoning) + `ask_human` (question) + `refuse` (reason)
- **Real tool schemas**: 从 `tool_schemas/<service>.json` 按服务加载；若 task YAML 有 `required_tools`，则优先缩小到该任务相关工具子集
- `build_tools_list(service, required_tools)` → flow tools + 当前任务相关 real tools

### Step 4: `prompts.py` — 两套 system prompt

- **Prompt A (bare)**: 最小化描述，要求第一步先 `predict_risk`，第二步自行选择动作
- **Prompt B (explicit_rules)**: 详细列出何时 ask_human / refuse / 执行，以及 risk judgment 与最终动作的一致性约束

### Step 5: `tasks.py` — 任务加载与划分

175 个 ask_human 任务按服务分布：

| 服务 | 数量 | | 服务 | 数量 |
|------|------|-|------|------|
| discourse | 18 | | openemr | 18 |
| erpnext | 28 | | owncloud | 12 |
| gitea | 23 | | rocketchat | 25 |
| mailu | 25 | | zammad | 19 |
| nocodb | 8 | | | |

按服务分层抽样，确保每个服务在 train/test 中都有代表。

### Step 6: `llm.py` — LLM 调用

- `call_with_tools()` — 单轮调用，tool_choice=required
- `call_with_tools_multi_turn()` — 多轮调用，传入完整 messages 历史
- 支持 OpenAI 兼容 API（OpenRouter/vLLM/本地部署均可）

### Step 7: `runner.py` — 核心 2-turn 流程

```
Turn 1: snapshot(need_risk) + task tool set → LLM
  └─ predict_risk(safe|risky) → Turn 2

Turn 2: updated snapshot(need_action) + same task tool set → LLM
  ├─ ask_human(question) → 结束
  ├─ refuse(reason) → 结束
  └─ real_tool(...) → 结束
```

### Step 8: `gen_traces.py` — 批量生成训练轨迹

当前更推荐直接复用 benchmark teacher 结果，避免重复生成成本：

```bash
python benchmark.py --models gpt54 --prompts explicit_rules --use-train
# 输出: results/gpt54_explicit_rules.jsonl (181 条，其中 160 条可蒸馏)
```

### Step 9: `export_sft.py` — 导出 LLaMA-Factory 格式

输出 LLaMA-Factory ShareGPT 格式 JSON：

```json
{
  "conversations": [
    {"from": "human", "value": "snapshot JSON"},
    {"from": "function_call", "value": "{\"name\": \"predict_risk\", \"arguments\": {...}}"},
    {"from": "observation", "value": "{tool result + updated context}"},
    {"from": "function_call", "value": "{\"name\": \"ask_human\", \"arguments\": {...}}"}
  ],
  "system": "explicit_rules prompt",
  "tools": "[{name, description, parameters}, ...]"
}
```

位置规则：奇数位 = human/observation，偶数位 = gpt/function_call

### Step 10-11: `evaluate.py` + `benchmark.py`

评估指标：accuracy, risk_detection_rate, consistency_rate, ask_rate, error_rate + 按服务分组

6 组实验矩阵：

| Model | Prompt A (bare) | Prompt B (explicit_rules) |
|-------|----------------|--------------------------|
| GPT-5.4 | 基线上界 | 基线上界 |
| Qwen base | 零样本 | 零样本+规则 |
| Qwen SFT | SFT 效果 | SFT+规则效果 |

---

## 关键设计决策

1. **Real tool schema 既作上下文也作候选动作空间**: predict_risk 需要引用 real tool name 和 args；第二轮真实工具也仍然对模型可见。若 task YAML 给出 `required_tools`，则优先使用 oracle 工具子集以减少上下文噪声。

2. **predict_risk 是结构化理由层，不再硬锁分支**: 第二轮仍然让模型自由选择 ask_human / refuse / real tool，用于同时评估最终决策和与 risk judgment 的一致性。

3. **无 memory 注入**: 去掉 plan_memory 和 tool_memory，纯测模型的即时判断能力，不依赖历史经验。

4. **SFT 训练用 Prompt B**: 训练数据统一用显式规则 prompt，让模型学到判断标准。benchmark 时切换 Prompt A/B 来对比 prompt 效果。

5. **train/test 按服务分层抽样**: 确保 9 个服务都在训练和测试中有代表。

---

## SFT 训练 (LLaMA-Factory)

### 训练流程

```bash
cd askbench/sft
source ~/miniconda3/etc/profile.d/conda.sh
bash setup.sh                                                    # 创建 conda 环境 + 安装依赖 + 链接数据
conda activate /home/hcj/pipeline/.conda-envs/askbench-qwen35
DISABLE_VERSION_CHECK=1 llamafactory-cli train train_lora_gpu_explicit160.yaml   # 当前主线 bf16 LoRA
DISABLE_VERSION_CHECK=1 llamafactory-cli train train_qlora_gpu.yaml               # 显存不足时回退
DISABLE_VERSION_CHECK=1 llamafactory-cli export merge_lora_explicit160.yaml      # 合并 adapter
DISABLE_VERSION_CHECK=1 llamafactory-cli chat inference_merged_explicit160.yaml  # 交互测试
```

### 训练配置

| 配置 | Mac MPS | GPU 集群 |
|------|---------|---------|
| 模型 | Qwen2.5-0.5B-Instruct (仅 smoke test) | Qwen3.5-9B |
| 模板 | qwen | qwen / qwen3_5 |
| 精度 | fp32 (MPS 不支持混合精度) | QLoRA 4bit / LoRA bf16 |
| Batch | 2 × 4 grad_accum = 有效 8 | 1 × 16 grad_accum = 有效 16 |
| Epochs | 5 | 5 |
| LoRA | rank=8, alpha=16, target=all | rank=32, alpha=64, target=all |
| LR | 1e-4, cosine schedule | 1.5e-4 (QLoRA) / 2e-4 (LoRA) |
| Context | 2048 | 6144 (QLoRA) / 4096 (LoRA) |

### 依赖版本

当前实现改为：

- Miniconda / Anaconda + conda 环境
- conda 环境默认 Python 3.11
- 先装仓库 `requirements.txt`
- `torch` 由训练机器按 CUDA 版本安装
- `LLaMA-Factory` 直接从 GitHub `main` 安装

注意：旧版 `llamafactory==0.9.4` + `cutoff_len=2048` 只适合本地小模型验证，不适合 Qwen3.5-9B 的正式实验。
另外，旧的非 `explicit160` bf16 train / merge / inference YAML 已经从仓库移除，避免误用旧链路。

---

## 建议增强 (可选)

**加入负样本提升精度测量**: 当前 175 个全是 ask_human 任务，只能测 recall。建议从 194 个 execute 任务中抽 20-30 个加入 test set 作为负样本，测量模型是否会误触 ask_human（false positive）。这样能计算真正的 precision/recall/F1。

---

## 关键复用文件

| 来源 | 复用内容 | 目标文件 |
|------|---------|---------|
| `safety_pipeline/runtime.py:628-696` | predict_risk + ask_human schema 定义 | `schemas.py` |
| `safety_pipeline/runtime.py:720-759` | snapshot 结构 (大幅精简) | `runner.py` |
| `safety_pipeline/runtime.py:1065-1105` | SFT system prompt (精简为 Prompt B) | `prompts.py` |
| `safety_pipeline/llm.py:288-351` | LLM 调用逻辑 (精简) | `llm.py` |
| `safety_pipeline/service_registry.py:18-91` | 服务 display_name/domain 映射 | `schemas.py` |
| `safety_pipeline/*_tools.py` → `get_all_schemas()` | 各服务 real tool schema | `tool_schemas/*.json` |
| `tasks/*/*.yaml` | 任务定义 (直接引用，不复制) | `tasks.py` |
