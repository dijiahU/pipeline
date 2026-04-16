# AskBench Cluster Usage

这份说明只覆盖当前保留的 `decision-token` 训练路径，不再包含旧的 benchmark、merged 模型验证或 vLLM 服务脚本。

## 1. 当前边界

- `askbench/` 现在只负责训练数据对接和 LoRA SFT。
- 集群上跑训练只需要：
  - `conda`
  - `Slurm`
  - GPU 节点
- 不需要 Docker，也不依赖 Singularity/Apptainer。

## 2. 首次准备

```bash
cd /home/hcj/pipeline/askbench/sft
bash setup.sh
python check_env.py
```

然后手动修改：

- `train_trl_decision_tokens.yaml`
  - 把 `model_name_or_path` 改成真实基础模型路径或 HF repo id

## 3. 交互式调试

先申请一个 GPU shell：

```bash
srun -N 1 -n 1 -p gpu --gres=gpu:1 \
  --cpus-per-task=4 --mem-per-cpu=2G \
  --time=00:30:00 --pty bash
```

进入节点后：

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate /home/hcj/pipeline/.conda-envs/askbench-decision-sft
cd /home/hcj/pipeline/askbench/sft
python check_env.py
python train_decision_tokens_trl.py --config train_trl_decision_tokens.yaml
```

## 4. 正式提交

```bash
cd /home/hcj/pipeline/askbench/sft
sbatch run_decision_token_train.slurm
```

默认日志位置：

- `askbench/sft/logs/<job>_<jobid>.out`
- `askbench/sft/logs/<job>_<jobid>.err`

## 5. 数据来源

训练前先确认主仓库已经导出了：

- `/home/hcj/pipeline/artifacts/decision_token_sft.json`

`setup.sh` 会把它链接成：

- `/home/hcj/pipeline/askbench/sft/pipeline_decision_token_sft.json`

## 6. 建议

- 先用更小的基础模型做 smoke，例如 3B 级别，再放大。
- 保持 `output_dir` 指向 `askbench/results/` 下的新目录，不要复用旧实验目录。
- 如果需要新的训练 profile，新增 yaml 即可，不要再恢复旧的大模型实验链。
