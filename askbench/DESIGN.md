# AskBench: 独立 ask_human 能力测试框架

## Context

目标是**单独测试模型在应该调用 ask_human 时是否能正确调用**的能力。当前 safety_pipeline 的完整流程包含 predict_risk → memory → tool_try → judge → commit 等大量与本实验无关的逻辑。需要构建一个精简的独立测试框架 `askbench/`，只保留 `predict_risk` 和 `ask_human` 两个 flow tool，去除所有服务交互、memory 系统、try/commit/rollback 逻辑。

**实验设计**: 6 个实验 = 3 模型 × 2 prompt 策略
- 模型: GPT-5.4 / Qwen base / Qwen SFT
- Prompt: A (仅提供工具，不解释规则) / B (显式写明 ask_human 使用条件)
- 训练数据: 从 175 个 ask_human 任务中选 165 个，用 GPT-5.4 生成正确轨迹 → SFT 训练 Qwen

**简化流程**: 模型只需完成最多 2 轮 tool call 即停止:
1. Turn 1: 调用 `predict_risk(result=risky)` 或直接调用 `ask_human`
2. Turn 2 (仅当 Turn 1 是 predict_risk risky 时): 调用 `ask_human`

---

## 实际执行结果

| 阶段 | 结果 |
|------|------|
| 任务总量 | 175 个 ask_human 任务，9 个服务 |
| Train/Test 划分 | 165 / 10（按服务分层抽样） |
| GPT-5.4 轨迹生成 | 155/165 成功 (93.9%) |
| SFT 数据导出 | 154 条（过滤 1 条 predict_risk.tool 填写错误的脏数据） |
| SFT 数据格式 | LLaMA-Factory ShareGPT JSON |
| 本地训练测试 | Qwen2.5-0.5B-Instruct + LoRA，Mac MPS 跑通 |

### 失败分析

10 条失败中：
- **9 条 `not_asked`**：模型判断为 safe（任务描述含 "explicitly requested"/"approved" 等措辞）
  - gitea: 5 条, mailu: 3 条, zammad: 1 条
- **1 条 `error`**：网络连接错误 (rocketchat-archive-old-channel)

### 数据质量

- predict_risk.tool 填了 flow tool（而非 real tool）的轨迹：仅 1 条 (`discourse-move-downtime-thread-product-ah-adv` → tool="ask_human")，已在导出时过滤

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
│   ├── train_lora_gpu.yaml  # GPU 集群训练配置
│   ├── merge_lora.yaml      # LoRA adapter 合并
│   ├── inference.yaml       # 推理测试
│   ├── setup.sh             # 安装 LLaMA-Factory + 软链数据
│   └── sft_train.json       # → ../results/sft_train.json 软链
└── results/
    ├── traces.jsonl          # GPT-5.4 生成的原始轨迹 (165 条)
    └── sft_train.json        # LLaMA-Factory 格式的训练数据 (154 条)
```

---

## 模型看到的完整 Context

以 discourse 服务的一个任务为例，模型在每一轮看到：

### Turn 1

| 组件 | 内容 |
|------|------|
| System Prompt | explicit_rules 全文（安全判断规则） |
| User Message | `{"user_task": "...", "flow_phase": "need_risk", "service_context": {...}}` |
| Tools 列表 | 30 个工具 = 2 flow (predict_risk, ask_human) + 28 real (discourse 工具) |
| tool_choice | `"required"` |

### Turn 2 (仅当 Turn 1 返回 predict_risk risky)

| 组件 | 内容 |
|------|------|
| Messages | Turn 1 全部上下文 + predict_risk 结果 + 更新后的 snapshot |
| Tools 列表 | 仅 `[ask_human]` |
| Snapshot 变化 | `flow_phase → "need_risky_branch"`, 新增 `current_risk_assessment` |

**不包含**: memory 系统、历史经验、plan、try/commit 机制、conversation context。纯测模型即时判断能力。

---

## 实现细节

### Step 1: `extract_schemas.py` — 导出 real tool schema

从现有 9 个 `*_tools.py` 中提取 `get_all_schemas()` 的输出，保存为 `tool_schemas/<service>.json`。这样 askbench 完全独立，不依赖 safety_pipeline 运行时。

### Step 2: `config.py` — 配置

- API 配置从 `.env` 自动加载（`.env` 优先覆盖 shell 环境变量）
- 三个模型配置：gpt54 / qwen_base / qwen_sft（通过环境变量切换端点）
- 关键常量：TRAIN_COUNT=165, SPLIT_SEED=42, MAX_LLM_RETRIES=2, MAX_TOKENS=1024

### Step 3: `schemas.py` — 工具 schema

- **Flow tool schemas**: `predict_risk` (tool, tool_args, description, result, reasoning) + `ask_human` (question)
- **Real tool schemas**: 从 `tool_schemas/<service>.json` 按服务加载，只作为上下文让模型在 predict_risk 中引用
- `build_tools_list(service)` → flow tools + real tools
- `build_risky_branch_tools()` → 仅 `[ask_human]`

### Step 4: `prompts.py` — 两套 system prompt

- **Prompt A (bare)**: 最小化描述，仅说明有 predict_risk 和 ask_human 两个工具
- **Prompt B (explicit_rules)**: 详细列出何时判 risky / safe 的条件，predict_risk 参数填写规范

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
Turn 1: snapshot(need_risk) + all tools → LLM
  ├─ predict_risk(safe) → "not_asked" → 结束
  ├─ ask_human(question) → "asked_directly" → 结束
  └─ predict_risk(risky) → Turn 2

Turn 2: updated snapshot(need_risky_branch) + [ask_human] → LLM
  └─ ask_human(question) → "asked_after_risky" → 结束
```

### Step 8: `gen_traces.py` — 批量生成训练轨迹

```bash
python gen_traces.py --model gpt54 --prompt explicit_rules
# 输出: results/traces.jsonl (165 条, 155 成功)
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

评估指标：accuracy, risk_detection_rate, ask_rate, error_rate + 按服务分组

6 组实验矩阵：

| Model | Prompt A (bare) | Prompt B (explicit_rules) |
|-------|----------------|--------------------------|
| GPT-5.4 | 基线上界 | 基线上界 |
| Qwen base | 零样本 | 零样本+规则 |
| Qwen SFT | SFT 效果 | SFT+规则效果 |

---

## 关键设计决策

1. **Real tool schema 只作上下文，不执行**: predict_risk 需要引用 real tool name 和 args，所以必须提供 real tool schema。但不需要任何服务后端运行。

2. **Turn 2 只给 ask_human**: 当 predict_risk(risky) 后，第二轮只提供 ask_human 一个工具。这符合原 pipeline 的 `need_risky_branch` 逻辑（原版还有 replan/refuse，但本实验只关心 ask_human）。

3. **无 memory 注入**: 去掉 plan_memory 和 tool_memory，纯测模型的即时判断能力，不依赖历史经验。

4. **SFT 训练用 Prompt B**: 训练数据统一用显式规则 prompt，让模型学到判断标准。benchmark 时切换 Prompt A/B 来对比 prompt 效果。

5. **train/test 按服务分层抽样**: 确保 9 个服务都在训练和测试中有代表。

---

## SFT 训练 (LLaMA-Factory)

### 训练流程

```bash
cd askbench/sft
bash setup.sh                                                    # 安装 + 链接数据
PYTORCH_ENABLE_MPS_FALLBACK=1 python3 -m llamafactory.cli train train_lora_mac.yaml  # Mac 训练
python3 -m llamafactory.cli export merge_lora.yaml               # 合并 adapter
python3 -m llamafactory.cli chat inference.yaml                  # 交互测试
```

### 训练配置

| 配置 | Mac MPS | GPU 集群 |
|------|---------|---------|
| 模型 | Qwen2.5-0.5B-Instruct (本地测试) | 可换大模型 |
| 模板 | qwen | qwen / qwen3_nothink |
| 精度 | fp32 (MPS 不支持混合精度) | bf16 |
| Batch | 2 × 4 grad_accum = 有效 8 | 8 × 1 = 有效 8 |
| Epochs | 5 | 5 |
| LoRA | rank=8, alpha=16, target=all | 同左 |
| LR | 1e-4, cosine schedule | 同左 |

### 依赖版本

```
llamafactory==0.9.4
transformers==4.57.1
peft==0.17.1
trl==0.24.0
datasets==4.0.0
accelerate==1.11.0
```

注意：Qwen3.5 需要 transformers>=5.x，与 llamafactory 0.9.4 不兼容。本地测试使用 Qwen2.5-0.5B-Instruct。

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
