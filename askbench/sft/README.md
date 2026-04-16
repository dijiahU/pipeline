# AskBench Decision-Token SFT

`askbench/sft/` 现在只保留一条 TRL 训练链：把 `artifacts/decision_token_sft.json` 训练成以决策 special token 开头的安全员监督数据。

## 保留文件

- `setup.sh`
  - 创建或复用 `askbench-decision-sft` conda 环境
  - 安装仓库依赖、PyTorch、TRL/Transformers/PEFT
  - 将 `../../artifacts/decision_token_sft.json` 链接为 `pipeline_decision_token_sft.json`
- `check_env.py`
  - 做最小 GPU 与 TRL 依赖检查
- `train_trl_decision_tokens.yaml`
  - 当前唯一训练配置
  - 需要手动把 `model_name_or_path` 改成真实基础模型
- `train_decision_tokens_trl.py`
  - 基于 `trl.SFTTrainer` 的 LoRA 训练入口
- `launch_decision_token_train.sh`
  - 单机或 `srun` 下的训练入口
- `run_decision_token_train.slurm`
  - 集群提交脚本

## 当前训练协议

- 数据格式是 TRL 的 conversational `prompt/completion`
- 输出以 4 个 special tokens 之一开头：
  - `<|direct_execute|>`
  - `<|ask_human|>`
  - `<|refuse|>`
  - `<|replan|>`
- 其后紧跟 reasoning-only JSON payload
- 主 agent 提出的 `tool` / `tool_args` 在 prompt 侧的 `assistant_proposed_tool_call` 提供，小模型不再重复生成它们
- 导出的 SFT 仍然是前缀展开的多轮轨迹
- 但每个 user snapshot 本身是 fresh-style 的当前决策快照
- 对当前 step 有帮助的最近少量真实工具历史会放在对应 snapshot 的 `prior_steps`
- `completion_only_loss=true`
  - 只对最后一条安全员输出计算 loss
- 当前默认训练配置除了 LoRA 之外，还会保留 `embed_tokens` 和 `lm_head` 为可训练/可保存模块
  - 目的不是改变阈值逻辑，而是让这 4 个 decision special tokens 的首位概率更可靠
  - 当前仓库里还没有最终确认目标 `Qwen3.5` 小模型应按 tied 还是 untied 处理，所以先采用这个更保守的方案

## 快速开始

```bash
cd /home/hcj/pipeline/askbench/sft
bash setup.sh
python check_env.py
```

修改 `train_trl_decision_tokens.yaml` 里的 `model_name_or_path` 后，可直接训练：

```bash
python train_decision_tokens_trl.py --config train_trl_decision_tokens.yaml
```

或提交到 Slurm：

```bash
sbatch run_decision_token_train.slurm
```

## 注意事项

- 训练数据来自仓库主流程导出的 `artifacts/decision_token_sft.json`。
- 部署时需要加载包含这 4 个 decision tokens 的 tokenizer。
- 训练基模型应优先选择自带 chat template 的 instruct 模型；若没有，需要在配置里提供 `chat_template_path`。
- 如果你后续明确确认所用 `Qwen3.5` checkpoint 是 tied weights，并且想进一步缩小训练面，可以再评估切到更轻的 token-only 方案；当前仓库默认不假设这一点。
