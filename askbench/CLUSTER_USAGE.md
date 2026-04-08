# AskBench 集群使用说明

更新时间：2026-04-08

本文档记录当前 AskBench 在这套本地 Slurm 集群上的实际使用方法。目标不是复述通用 Slurm 教程，而是把这次已经跑通过、已经踩过坑的内容固定下来，保证下次进入项目时可以直接照着做。

---

## 1. 先说结论

当前需要同时参考两类信息：

- 本仓库里的本地教程：[ASPIRE_slurm_tutorial.md](/home/hcj/pipeline/askbench/ASPIRE_slurm_tutorial.md)
- 学院集群手册：http://10.15.89.177:8889/

但二者不是同一套 Slurm 部署。

正确理解方式：

- 学院手册提供的是 Slurm 的通用原则
- 你当前真正连上的，是另一套本地 Slurm 集群
- 真正提交任务时，参数必须以当前机器实时查询结果为准

对当前 AskBench 微调任务，已经验证通过的路线是：

1. 在登录节点 `manage` 上准备 conda 环境、模型、脚本
2. 用 `srun --pty bash` 进入计算节点做交互式调试
3. 通过后再用 `sbatch` 提交正式训练
4. 正式训练默认使用单卡 A40 48GB 上的 bf16 LoRA 配置

---

## 2. 学院集群和当前本地集群的关系

### 2.1 共性

两者都使用 Slurm，所以这些原则是一样的：

- 不要在登录节点上跑长时间训练
- GPU 任务要通过 `sbatch`、`srun`、`salloc` 申请资源
- `run.slurm` 本质上是带 `#SBATCH` 资源声明的 bash 脚本
- 作业脚本里要显式初始化 conda
- 标准输出和标准错误都要写日志

### 2.2 差异

学院手册里的示例环境和当前本地集群不同，主要差异如下。

| 项目 | 学院集群手册 | 当前本地集群实测 |
|---|---|---|
| 分区名 | `normal` / `critical` / `ShangHAI` / `cpu` 等 | 只有 `gpu` |
| 账号参数 | 某些分区需要 `-A 组名-队列名` | 当前 `gpu` 分区 `AllowAccounts=ALL`，不需要 `-A` |
| 节点命名 | `ai_gpu*`、`sist_gpu*` 等 | `gpu1`、`gpu2`、`gpu3`、`gpu4` |
| GPU 规格 | 学院大集群的多种卡型 | 当前本地集群实测为 A40 48GB |
| 直接 SSH 计算节点 | 手册写的是“有任务时可以登录” | 当前本地集群实测即使有作业，`ssh gpu1` 仍然被拒绝 |

结论：

- 学院手册里的 Slurm 概念可以学
- 但具体参数必须优先以当前本地集群的实时结果为准

---

## 3. 当前本地集群的实测信息

以下信息不是推测，而是本次任务里已经实际查过的结果。

### 3.1 登录节点

当前登录节点主机名：

```bash
hostname
```

结果：

```text
manage
```

可以把 `manage` 视为当前这套集群的登录/管理节点。

### 3.2 分区和节点

实际查询过的命令：

```bash
scontrol show partition
sinfo -N
sinfo -O Partition,NodeHost,Gres,GresUsed,StateLong
```

实测结论：

- 当前只有一个分区：`gpu`
- 当前分区 `AllowAccounts=ALL`
- 当前节点为：`gpu1`、`gpu2`、`gpu3`、`gpu4`
- 每个节点有 `gpu:4`

### 3.3 GPU 规格

通过最小 `srun` 探测作业查询：

```bash
srun -N 1 -n 1 -p gpu --gres=gpu:1 --time=00:01:00 \
  nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
```

结果显示：

```text
NVIDIA A40, 46068 MiB
```

因此当前可用 GPU 为 A40 48GB。

### 3.4 当前用户 `hcj` 的权限

已经验证的事实如下：

- 可以向 `gpu` 分区提交作业
- 可以通过 `srun` 获取 GPU 交互式资源
- 可以通过 `sbatch` 正式提交训练
- 不能直接 `ssh gpu1`
- 即使已有运行中的 GPU 作业，`ssh gpu1` 仍然 `Permission denied`

因此当前集群的正确用法是：

- 正式训练用 `sbatch`
- 交互式调试用 `srun --pty bash`
- 不要把“直接 ssh 到计算节点”当作默认流程

---

## 4. 登录节点、计算节点、调试节点的分工

### 4.1 登录节点 `manage` 负责什么

登录节点负责：

- 改代码
- 改训练 YAML
- 创建和维护 conda 环境
- 从 Hugging Face 或镜像下载模型到共享目录
- 提交 `sbatch`
- 查看日志
- 启动交互式 `srun --pty bash`

不应该在登录节点做：

- 长时间训练
- 长时间占用 GPU
- 高负载计算或高负载编译

### 4.2 计算节点 `gpu1-4` 负责什么

计算节点负责：

- 真正加载 GPU
- 实际运行训练
- 交互式验证环境和训练命令

### 4.3 为什么这里不能直接 `ssh gpu1`

当前本地集群里，实测：

```bash
ssh gpu1 hostname
```

返回：

```text
Permission denied
```

所以正确流程不是：

1. 登录节点
2. 手工 ssh 到计算节点
3. 在计算节点跑训练

而是：

1. 登录节点
2. 通过 `srun` 或 `sbatch` 向 Slurm 申请资源
3. Slurm 在计算节点上为你启动进程

---

## 5. AskBench 当前环境、模型和训练配置

### 5.1 conda 环境

当前 AskBench conda 环境路径：

```text
/home/hcj/pipeline/.conda-envs/askbench-qwen35
```

激活方式：

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate /home/hcj/pipeline/.conda-envs/askbench-qwen35
```

### 5.2 当前 PyTorch 版本

这一点非常重要。当前已经验证可用的版本组合是：

- `torch 2.10.0+cu128`
- `torchvision 0.25.0+cu128`
- `torchaudio 2.10.0+cu128`

原因：

- 之前错误装成了 `cu130`
- 当前计算节点驱动对应 `CUDA 12.8`
- `cu130` 会导致 `torch.cuda.is_available()` 为 `False`

因此当前环境必须保持 `cu128`。

### 5.3 当前模型目录

当前训练不再直接从 Hugging Face 在线拉模型，而是使用本地共享路径：

```text
/home/hcj/pipeline/models/Qwen3.5-9B
```

原因：

- 登录节点可以访问外网或镜像
- 计算节点访问 `huggingface.co` 时实测会 `Connection refused`

因此当前正确做法是：

1. 在登录节点下载模型到共享目录
2. 训练配置里的 `model_name_or_path` 写成本地目录
3. 计算节点从本地共享目录读取模型

如果以后要重新下载，推荐在登录节点使用镜像：

```bash
HF_ENDPOINT=https://hf-mirror.com hf download Qwen/Qwen3.5-9B \
  --local-dir /home/hcj/pipeline/models/Qwen3.5-9B
```

### 5.4 当前默认训练规格

当前默认正式训练配置文件是：

- [train_lora_gpu.yaml](/home/hcj/pipeline/askbench/sft/train_lora_gpu.yaml)

这不是 QLoRA，而是已经验证可启动的 bf16 LoRA 配置，针对单张 A40 48GB 收紧过参数。

当前关键参数为：

- 基座模型：`/home/hcj/pipeline/models/Qwen3.5-9B`
- 模板：`template: qwen3_5`
- 微调方式：`finetuning_type: lora`
- 精度：`bf16: true`
- `lora_rank: 32`
- `lora_alpha: 64`
- `lora_dropout: 0.05`
- `per_device_train_batch_size: 1`
- `gradient_accumulation_steps: 16`
- `cutoff_len: 4096`
- `gradient_checkpointing: true`

对应输出目录：

```text
/home/hcj/pipeline/askbench/results/qwen35_9b_lora_adapter
```

### 5.5 bf16 LoRA 是否已经验证过

已经验证过。

在交互式计算节点里，下面这条命令已经成功启动训练并进入 step 进度：

```bash
DISABLE_VERSION_CHECK=1 llamafactory-cli train train_lora_gpu.yaml
```

实际看到过的训练启动信息包括：

- `Num examples = 154`
- `Num Epochs = 5`
- `Total optimization steps = 50`
- `Number of trainable parameters = 86,556,672`

因此，当前这套 bf16 LoRA 配置在单张 A40 48GB 上已经验证能启动。

补充说明：

- 之前为了打通全流程，仓库里一度使用过 `qwen3_5_nothink`
- 当前默认训练、adapter 推理、merge 和 merged 推理都已经切回 `qwen3_5`
- 因此后续默认路线应视为 thinking 模型路线

### 5.6 如果想切回 QLoRA

仓库里仍然保留了：

- [train_qlora_gpu.yaml](/home/hcj/pipeline/askbench/sft/train_qlora_gpu.yaml)

如果未来遇到显存瓶颈，可以切回 QLoRA。但当前默认正式训练入口已经切到 bf16 LoRA。

---

## 6. 当前 Slurm 脚本怎么工作

### 6.1 提交脚本

文件：

- [run_askbench_qwen35_train.slurm](/home/hcj/pipeline/askbench/sft/run_askbench_qwen35_train.slurm)

当前核心逻辑：

- `#SBATCH --partition=gpu`
- `#SBATCH --gres=gpu:1`
- `#SBATCH --cpus-per-task=4`
- `#SBATCH --mem-per-cpu=2G`
- `#SBATCH --time=2-00:00:00`
- 显式激活 conda
- 默认 `ASKBENCH_TRAIN_CONFIG=train_lora_gpu.yaml`
- 通过 `srun` 启动 `launch_askbench_qwen35_train.sh`

### 6.2 启动脚本

文件：

- [launch_askbench_qwen35_train.sh](/home/hcj/pipeline/askbench/sft/launch_askbench_qwen35_train.sh)

它负责：

- 进入 `askbench/sft`
- 激活 conda
- 检查训练 YAML 是否存在
- 检查 `model_name_or_path` 是否仍是占位符
- 自动解析当前 Slurm 分配到的 GPU index
- 自动设置 `CUDA_VISIBLE_DEVICES`
- 打印主机名、日期、训练配置
- 运行 `python check_env.py`
- 最后执行 `llamafactory-cli train`

### 6.3 为什么还要手动绑定 GPU

当前本地 Slurm 部署里，交互式 shell 下 `nvidia-smi` 可能还能看到全部 4 张卡，不代表这 4 张卡都属于你。

真正属于你的 GPU，需要看 Slurm 分配结果，例如：

```bash
scontrol show job -d 1575
```

如果里面出现：

```text
GRES=gpu:1(IDX:0)
```

就表示这次作业分到的是 0 号卡。

批量作业里，`launch_askbench_qwen35_train.sh` 已经自动处理这个问题。  
交互式调试时，建议你手动执行：

```bash
export CUDA_VISIBLE_DEVICES=<IDX>
```

例如：

```bash
export CUDA_VISIBLE_DEVICES=0
```

---

## 7. 正确的交互式调试方法

### 7.1 申请交互式计算节点

在登录节点执行：

```bash
srun -N 1 -n 1 -p gpu --gres=gpu:1 \
  --cpus-per-task=4 --mem-per-cpu=2G \
  --time=00:30:00 --pty bash
```

这条命令会给你：

- 1 张 GPU
- 4 个 CPU
- 一台计算节点上的交互式 bash

### 7.2 进入后先做什么

先确认你确实已经在计算节点：

```bash
hostname
nvidia-smi
```

然后在登录节点或另一个终端确认这次作业的 job id：

```bash
squeue -u "$USER"
```

再用 job id 查询具体分到哪张卡：

```bash
scontrol show job -d <jobid>
```

看这一段：

```text
GRES=gpu:1(IDX:0)
```

这里的 `IDX:0` 就表示你拿到的是 0 号卡。

然后在交互式 shell 里手动固定：

```bash
export CUDA_VISIBLE_DEVICES=0
```

### 7.3 激活环境并验证 GPU

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate /home/hcj/pipeline/.conda-envs/askbench-qwen35
cd /home/hcj/pipeline/askbench/sft
python check_env.py
python -c "import torch; print('torch:', torch.__version__); print('cuda:', torch.version.cuda); print('cuda_available:', torch.cuda.is_available()); print('device_count:', torch.cuda.device_count()); print('device_0:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A')"
```

当前已经验证通过的正常结果应接近：

```text
torch: 2.10.0+cu128
cuda: 12.8
cuda_available: True
device_count: 1
device_0: NVIDIA A40
```

### 7.4 验证模型本地路径可加载

```bash
python -c "from transformers import AutoConfig; cfg = AutoConfig.from_pretrained('/home/hcj/pipeline/models/Qwen3.5-9B', trust_remote_code=True); print(type(cfg).__name__)"
```

当前已经验证通过，正常输出为：

```text
Qwen3_5Config
```

### 7.5 验证训练命令本身

```bash
cd /home/hcj/pipeline/askbench/sft
DISABLE_VERSION_CHECK=1 llamafactory-cli train train_lora_gpu.yaml
```

这一步的目标不是跑完整训练，而是确认：

- YAML 可以正常读取
- 数据集可以正常识别
- 本地模型可以正常加载
- Trainer 可以正常初始化
- 显存不会一启动就 OOM

如果看到已经进入训练 step，就说明主链路打通，可以 `Ctrl+C` 停掉，回登录节点正式 `sbatch`。

---

## 8. 正式训练流程

### 8.1 提交前检查

在登录节点执行：

```bash
cd /home/hcj/pipeline/askbench/sft
source ~/miniconda3/etc/profile.d/conda.sh
conda activate /home/hcj/pipeline/.conda-envs/askbench-qwen35
python check_env.py
```

如果登录节点显示 `CUDA: unavailable`，这是正常的，因为登录节点不承担训练。

### 8.2 提交正式训练

```bash
cd /home/hcj/pipeline/askbench/sft
sbatch run_askbench_qwen35_train.slurm
```

### 8.3 如果想临时改用别的配置

例如临时切回 QLoRA：

```bash
cd /home/hcj/pipeline/askbench/sft
ASKBENCH_TRAIN_CONFIG=train_qlora_gpu.yaml sbatch run_askbench_qwen35_train.slurm
```

### 8.4 查看队列和任务详情

```bash
squeue -u "$USER"
scontrol show job <jobid>
```

### 8.5 取消任务

```bash
scancel <jobid>
```

---

## 9. 如何看训练过程

### 9.1 先看任务是不是还活着

最先看的不是 loss，而是 Slurm 状态：

```bash
squeue -u "$USER"
scontrol show job <jobid>
```

如果任务仍然是 `RUNNING`，且没有非零 `ExitCode`，说明作业没有停。

### 9.2 当前这套作业的日志分工

当前 Slurm 脚本里：

- 标准输出写到 `.out`
- 标准错误写到 `.err`

日志路径格式：

- `/home/hcj/pipeline/askbench/sft/logs/%x_%j.out`
- `/home/hcj/pipeline/askbench/sft/logs/%x_%j.err`

在这次实际训练里，重要经验是：

- `.out` 主要是启动信息
- 真正的 trainer 日志和大部分模型加载信息主要落在 `.err`

所以真正看训练过程时，应该优先看：

```bash
tail -f /home/hcj/pipeline/askbench/sft/logs/<job_name>_<jobid>.err
```

再辅助看：

```bash
tail -f /home/hcj/pipeline/askbench/sft/logs/<job_name>_<jobid>.out
```

### 9.3 为什么有时看不到精确的 `x/50`

LLaMA-Factory / transformers 的 tqdm 进度条不一定会稳定刷到日志文件里。  
因此可能出现这种情况：

- `squeue` 显示作业还在跑
- 日志里已经有 `***** Running training *****`
- 但文件里没有清晰的 `12/50`、`13/50`

这不等于任务挂了，只说明进度条没有完整持久化到日志文件。

如果想确认有没有明显报错，先看：

```bash
tail -n 200 /home/hcj/pipeline/askbench/sft/logs/<job_name>_<jobid>.err
```

### 9.4 当前输出目录

当前默认输出目录：

```text
/home/hcj/pipeline/askbench/results/qwen35_9b_lora_adapter
```

训练过程中和训练结束后，常见产物会出现在这里，例如：

- checkpoint 目录
- `trainer_state.json`
- loss 图
- LoRA adapter 权重

---

## 10. LLaMA-Factory 可视化和 TensorBoard

### 10.1 当前状态

当前 [train_lora_gpu.yaml](/home/hcj/pipeline/askbench/sft/train_lora_gpu.yaml) 已经调整为：

- `plot_loss: true`
- `report_to: tensorboard`
- `logging_steps: 1`
- `save_steps: 10`

并且 `tensorboard` 已经安装到 conda 环境里。

这表示：

- 未来新提交的训练会写 TensorBoard 日志
- 未来新提交的训练会更频繁地落日志和 checkpoint
- 训练结束后也会生成 loss 曲线图

### 10.2 一个重要区别

已经在跑的作业不会自动继承这些新配置。

也就是说：

- 配置改动只对“改动之后新提交的作业”生效
- 在修改前就已经启动的作业，仍然按旧配置运行

### 10.3 TensorBoard 怎么看

对新的训练作业，在登录节点执行：

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate /home/hcj/pipeline/.conda-envs/askbench-qwen35
tensorboard --logdir /home/hcj/pipeline/askbench/results/qwen35_9b_lora_adapter --port 6006
```

然后根据本机访问方式转发或打开对应端口。

### 10.4 不开 TensorBoard 时怎么快速看

如果只是想快速抓关键行：

```bash
rg -n "Running training|loss|train_runtime|saving" /home/hcj/pipeline/askbench/sft/logs/<job_name>_<jobid>.err
```

---

## 11. 常见问题和处理方法

### 11.1 登录节点 `python check_env.py` 显示 `CUDA: unavailable`

如果是在登录节点上看到，正常。

原因：

- 登录节点不负责训练
- 登录节点没有必要能访问 GPU

只有进入计算节点后再看 `nvidia-smi` 和 `torch.cuda.is_available()` 才有意义。

### 11.2 `ssh gpu1` 被拒绝

这是当前本地集群的预期行为，不是你操作错了。

正确方案：

- 正式训练：`sbatch`
- 交互式调试：`srun --pty bash`

### 11.3 计算节点无法访问 Hugging Face

当前已经实测：

- 登录节点可以访问 Hugging Face 或镜像
- 计算节点访问 `huggingface.co` 会 `Connection refused`

正确方案：

1. 在登录节点下载模型到共享目录
2. 训练配置里的 `model_name_or_path` 写本地路径

不要指望计算节点运行时再去联网下载模型。

### 11.4 `torch.cuda.is_available()` 为 `False`

如果你已经在计算节点上，`nvidia-smi` 也能看到 GPU，但 Python 仍然报：

- `cuda_available: False`
- 或提示驱动过旧

通常说明 PyTorch CUDA wheel 装错了。

当前这套机器上，已经踩过一次这个坑：

- 错误情况：装成 `cu130`
- 正确情况：改成 `cu128`

修复命令：

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate /home/hcj/pipeline/.conda-envs/askbench-qwen35
python -m pip uninstall -y torch torchvision torchaudio
python -m pip install torch==2.10.0 torchvision==0.25.0 torchaudio==2.10.0 \
  --index-url https://download.pytorch.org/whl/cu128
```

### 11.5 显存不足

当前默认已经是为单张 A40 48GB 收紧过的 bf16 LoRA 配置。

如果仍然 OOM，优先顺序是：

1. 把 [train_lora_gpu.yaml](/home/hcj/pipeline/askbench/sft/train_lora_gpu.yaml) 的 `cutoff_len` 从 `4096` 降到 `3072`
2. 如果还是不稳，再切回 [train_qlora_gpu.yaml](/home/hcj/pipeline/askbench/sft/train_qlora_gpu.yaml)

不建议第一步就改 LoRA rank。

### 11.6 日志里没有明显 step 数字

如果：

- `squeue` 显示作业仍在 `RUNNING`
- `scontrol show job` 没有失败退出
- `.err` 里也没有新的异常堆栈

那么即使没有清晰的 `x/50`，也不能据此判断训练已经挂掉。

当前这套日志里，step 进度条不一定会完整刷到文件。

---

## 12. 当前 AskBench 相关文件总览

环境与说明：

- [setup.sh](/home/hcj/pipeline/askbench/sft/setup.sh)
- [README.md](/home/hcj/pipeline/askbench/sft/README.md)
- [check_env.py](/home/hcj/pipeline/askbench/sft/check_env.py)
- [CLUSTER_USAGE.md](/home/hcj/pipeline/askbench/CLUSTER_USAGE.md)

训练与导出：

- [train_qlora_gpu.yaml](/home/hcj/pipeline/askbench/sft/train_qlora_gpu.yaml)
- [train_lora_gpu.yaml](/home/hcj/pipeline/askbench/sft/train_lora_gpu.yaml)
- [merge_lora.yaml](/home/hcj/pipeline/askbench/sft/merge_lora.yaml)
- [inference.yaml](/home/hcj/pipeline/askbench/sft/inference.yaml)
- [inference_merged.yaml](/home/hcj/pipeline/askbench/sft/inference_merged.yaml)
- [validate_merged_inference.py](/home/hcj/pipeline/askbench/sft/validate_merged_inference.py)
- [merge_and_validate_merged.sh](/home/hcj/pipeline/askbench/sft/merge_and_validate_merged.sh)

Slurm：

- [run_askbench_qwen35_train.slurm](/home/hcj/pipeline/askbench/sft/run_askbench_qwen35_train.slurm)
- [launch_askbench_qwen35_train.sh](/home/hcj/pipeline/askbench/sft/launch_askbench_qwen35_train.sh)
- [logs](/home/hcj/pipeline/askbench/sft/logs)

模型与结果：

- `/home/hcj/pipeline/models/Qwen3.5-9B`
- `/home/hcj/pipeline/askbench/results/qwen35_9b_lora_adapter`
- `/home/hcj/pipeline/askbench/results/qwen35_9b_merged`

---

## 13. 建议的实际使用顺序

### 第一步：在登录节点检查环境

```bash
cd /home/hcj/pipeline/askbench/sft
source ~/miniconda3/etc/profile.d/conda.sh
conda activate /home/hcj/pipeline/.conda-envs/askbench-qwen35
python check_env.py
```

### 第二步：如果模型还没下，在登录节点下载模型

```bash
HF_ENDPOINT=https://hf-mirror.com hf download Qwen/Qwen3.5-9B \
  --local-dir /home/hcj/pipeline/models/Qwen3.5-9B
```

### 第三步：交互式调试一次

```bash
srun -N 1 -n 1 -p gpu --gres=gpu:1 \
  --cpus-per-task=4 --mem-per-cpu=2G \
  --time=00:30:00 --pty bash
```

进入后：

```bash
hostname
nvidia-smi
```

然后在登录节点查 job id 和 GPU index：

```bash
squeue -u "$USER"
scontrol show job -d <jobid>
```

在交互式 shell 里固定卡：

```bash
export CUDA_VISIBLE_DEVICES=<IDX>
```

再执行：

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate /home/hcj/pipeline/.conda-envs/askbench-qwen35
cd /home/hcj/pipeline/askbench/sft
python check_env.py
python -c "from transformers import AutoConfig; cfg = AutoConfig.from_pretrained('/home/hcj/pipeline/models/Qwen3.5-9B', trust_remote_code=True); print(type(cfg).__name__)"
DISABLE_VERSION_CHECK=1 llamafactory-cli train train_lora_gpu.yaml
```

### 第四步：确认无误后正式提交

```bash
cd /home/hcj/pipeline/askbench/sft
sbatch run_askbench_qwen35_train.slurm
```

### 第五步：看状态和日志

```bash
squeue -u "$USER"
scontrol show job <jobid>
tail -f /home/hcj/pipeline/askbench/sft/logs/<job_name>_<jobid>.err
tail -f /home/hcj/pipeline/askbench/sft/logs/<job_name>_<jobid>.out
```

### 第六步：如果是新训练，起 TensorBoard

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate /home/hcj/pipeline/.conda-envs/askbench-qwen35
tensorboard --logdir /home/hcj/pipeline/askbench/results/qwen35_9b_lora_adapter --port 6006
```

### 第七步：训练完成后合并 LoRA

当前可复用脚本：

- [merge_lora.yaml](/home/hcj/pipeline/askbench/sft/merge_lora.yaml)
- [merge_and_validate_merged.sh](/home/hcj/pipeline/askbench/sft/merge_and_validate_merged.sh)

推荐直接在计算节点执行：

```bash
srun -N 1 -n 1 -p gpu --gres=gpu:1 \
  --cpus-per-task=4 --mem-per-cpu=2G \
  --time=01:30:00 \
  bash /home/hcj/pipeline/askbench/sft/merge_and_validate_merged.sh
```

这一步会做两件事：

1. 把 [qwen35_9b_lora_adapter](/home/hcj/pipeline/askbench/results/qwen35_9b_lora_adapter) 合并进基座模型
2. 立即加载 merged 模型，跑一条最小验证推理

合并后的目录是：

```text
/home/hcj/pipeline/askbench/results/qwen35_9b_merged
```

### 第八步：查看 merged 模型和验证结果

合并后会产出：

- merged 模型权重分片
- `model.safetensors.index.json`
- `tokenizer_config.json`
- `processor_config.json`
- `validation_response.json`

查看方式：

```bash
find /home/hcj/pipeline/askbench/results/qwen35_9b_merged -maxdepth 2 -type f | sort
cat /home/hcj/pipeline/askbench/results/qwen35_9b_merged/validation_response.json
```

需要注意：

- 当前 `validate_merged_inference.py` 走的是 `transformers` 直接加载 merged 模型
- 这条验证的目的主要是确认 merged 模型可以正常加载并生成
- 如果想要更贴近 LLaMA-Factory 模板效果，后续应优先使用 [inference_merged.yaml](/home/hcj/pipeline/askbench/sft/inference_merged.yaml) 配合 `llamafactory-cli chat`

---

## 14. 信息来源

### 14.1 本仓库文档

- [ASPIRE_slurm_tutorial.md](/home/hcj/pipeline/askbench/ASPIRE_slurm_tutorial.md)

### 14.2 学院集群手册

- 首页：http://10.15.89.177:8889/
- 作业：http://10.15.89.177:8889/job/index.html
- 登录和调试：http://10.15.89.177:8889/login/index.html
- 系统与资源：http://10.15.89.177:8889/system/index.html
- 帐号和权限：http://10.15.89.177:8889/accounts/index.html

### 14.3 官方模型信息

- Qwen3.5 Transformers 文档：https://huggingface.co/docs/transformers/main/en/model_doc/qwen3_5
- Qwen3.5-9B 模型页：https://huggingface.co/Qwen/Qwen3.5-9B

### 14.4 当前本地集群的实测命令

本文档中的本地集群结论来自对以下命令的实际查询和实际训练：

```bash
hostname
id
scontrol show partition
sinfo -N
sinfo -O Partition,NodeHost,Gres,GresUsed,StateLong
squeue -u "$USER"
scontrol show job -d <jobid>
srun -N 1 -n 1 -p gpu --gres=gpu:1 --time=00:01:00 nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
ssh gpu1 hostname
DISABLE_VERSION_CHECK=1 llamafactory-cli train train_lora_gpu.yaml
sbatch run_askbench_qwen35_train.slurm
```

需要注意：Slurm 排队状态和资源占用是实时变化的。如果后续集群升级或管理员修改了分区配置，应重新执行上述命令再核对。
