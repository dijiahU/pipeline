# AskBench Decision-Token SFT

`askbench/sft/` 现在只保留一条训练链：把 `artifacts/decision_token_sft.json` 训练成以决策 special token 开头的监督数据。

## 保留文件

- `setup.sh`
  - 创建或复用 `askbench-decision-sft` conda 环境
  - 安装仓库依赖、PyTorch、LLaMA-Factory
  - 将 `../../artifacts/decision_token_sft.json` 链接为 `pipeline_decision_token_sft.json`
- `check_env.py`
  - 只做最小 GPU 环境检查
- `train_lora_gpu_decision_tokens.yaml`
  - 当前唯一训练配置
  - 需要手动把 `model_name_or_path` 改成真实基础模型
- `launch_decision_token_train.sh`
  - 单机或 `srun` 下的训练入口
- `run_decision_token_train.slurm`
  - 集群提交脚本

## 当前训练协议

- 输出以 4 个 special tokens 之一开头：
  - `<|direct_execute|>`
  - `<|ask_human|>`
  - `<|refuse|>`
  - `<|replan|>`
- 其后紧跟 branch-specific JSON payload
- 训练配置已启用：
  - `resize_vocab: true`
  - `add_special_tokens`
  - `additional_target: "embed_tokens,lm_head"`

## 快速开始

```bash
cd /home/hcj/pipeline/askbench/sft
bash setup.sh
python check_env.py
```

修改 `train_lora_gpu_decision_tokens.yaml` 里的 `model_name_or_path` 后，可直接训练：

```bash
DISABLE_VERSION_CHECK=1 llamafactory-cli train train_lora_gpu_decision_tokens.yaml
```

或提交到 Slurm：

```bash
sbatch run_decision_token_train.slurm
```

## 注意事项

- 训练数据来自仓库主流程导出的 `artifacts/decision_token_sft.json`。
- 部署时需要加载包含这 4 个 decision tokens 的 tokenizer。
- 在线推理应关闭 thinking / reasoning 前缀，否则首 token 概率就不再干净。
