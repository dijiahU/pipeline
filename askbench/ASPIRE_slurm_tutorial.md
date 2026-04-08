# ASPIRE Slurm Tutorial

- 通过简单例子理解 `run.slurm` 是什么、为什么要用slurm提交任务
- 正确管理日志、conda 环境和 GPU 资源
- 详细内容可以参考学院集群的使用手册 http://10.15.89.177:8889/

---

## 1. `run.slurm`文件是什么，为什么需要它，以及怎么用

`run.slurm` 是一个Slurm 作业脚本，本质上是一个 **带有资源声明的 bash 脚本**。

1. 与`.sh`文件相同
    - 会以`#!/bin/bash`开头
    - 可以像正常的`.sh`文件一样，执行bash命令，例如`export`, `source`, `python`等;配合`srun`命令可以进一步明确资源要求
    - 涉及slurm相关命令时，会在这一行的开头添加`#SBATCH`明确
2. 负责Slurm相关的资源声明命令
    - 可以通过`#SBATCH`相关命令明确资源声明的指令，包括以下
    - 哪个 partition
    - 多少 CPU / 内存 / GPU
    - 最长运行时间
    - 任务名称
3. Slurm 解决“多人共享资源”的系统性问题
    - 原先没有slurm配置条件下，可能出现多个用户无意间同时使用同一张gpu的问题，导致显存溢出
    - 如果代码中没有配备清楚，可能产生日志缺失等问题
    - (配好GRES后) Slurm能够做到每个作业被分配到独占的 GPU 配额
    - 举例来说，(配置好GRES后)作业可以通过 `--gres=gpu:N` 申请 GPU 配额；Slurm 会在满足资源的节点上分配对应数量的 GPU，并通过 `CUDA_VISIBLE_DEVICES` 等机制限制作业只看到被分配的 GPU
4. 使用方式非常简单，可以认为它就是一个.sh文件，下面提供一个最简单版本的run.slurm用法

对于下面这个文件为例，在写好这个`run.slurm`文件后，
```bash
#!/bin/bash
#SBATCH --job-name=zyk_job42

python main.py
```
只需要在terminal中执行以下命令即可
```bash
sbatch run.slurm
```
之后slurm会自动调度这个任务，如果资源空闲或者排队等到这个提交的任务后，就会安排执行，且会自动收集stdout以及stderr

## 2. 如何配置新账号conda

各位的新账号是只装了cuda驱动，但可能需要自己装一下conda  
命令也很简答，可以参考如下
```bash
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh
source ~/.bashrc
```

## 3. 如何写具体的 `#SBATCH` 指令

首先需要注意的是，`#SBATCH` 行是Slurm 的作业参数声明，其**在提交时即刻生效**；  
但是，`run.slurm`文件中其余的bash命令只有在作业开始运行后生效；  
也就是说，如果需要涉及到排队情景，我想通过`run.slurm`调用一个固定版本的`main.sh`，但是提交任务之后我又继续修改这个文件，等到提交的slurm排队排到之后，它会执行这个`main.sh`文件的修改后的版本。  

所以，我个人比较推荐的一个比较好的实践就是每个提交的任务固定一个单独的`.sh`文件，这样才能避免文件版本错误。


### 3.1 partition与gres

对于partition, 实验室集群可以直接直接使用
```bash
#SBATCH -p gpu
```

学院集群会有更复杂的partition分类，详情可以查阅学院指南

关于gres, 在之后其配置完毕后，大家可以不用再制定具体哪一个节点以及哪一张卡，而是直接使用类似于以下命令
```bash
#SBATCH --gres=gpu:1
```
slurm系统就会自动找一个节点与一张空闲的gpu，也不需要再设置`CUDA_VISIBLE_DEVICES`这种环境变量

目前实验室集群尚未启用 GPU GRES, 可以通过以下命令检查
`scontrol show node`

### 3.2 `--job-name`：给作业一个“可读的名字”

```bash
#SBATCH --job-name=zyk_gcg_job123
```

- 这一规定的job_name可以被`%x`解析，可以用作之后的-o -e路径中
- 推荐在`--job-name`中包含个人信息，以及具体实验设置，例如我这边给的例子，意为zyk用户所执行的gcg实验的第123次提交

### 3.3 `--output` 和 `--error`

```bash
#SBATCH --output=output_%j.out
#SBATCH --error=error_%j.err
```

* **stdout（标准输出）** → `output_%j.txt`
* **stderr（标准错误）** → `error_%j.txt`
* `%j` 会被替换为Job ID， 这是一个由slurm自己生成的数字id
* `%x` 会被替换为Job Name，这是上一步由提交用户自己规定的

一个个人更推荐的实践是直接规定日志的路径，例如在通过`mkdir logs`创建好logs路径后，可以

```bash
#SBATCH --output=logs/%x_%j.out
#SBATCH --error=logs/%x_%j.err
```

或者本身每一次提交任务的`run.slurm`文件都单独在一个folder下，这样就不需要再开一个logs路径了。

### 3.4 `--time` 单次任务最长运行时间

```bash
#SBATCH --time=1-12:34:56
```

* 作业 **最多运行一天又12小时34分钟56秒**
* 超时后会被 Slurm **强制终止**
* 之后实验室slurm会仿照学院集群，设置单个任务最大运行时间

### 3.5 `--cpus-per-task` cpu核心数

对于大部分实验所涉及的gpu密集任务，这一项命令默认用以下设置即可，cpu核心数可能影响诸如 `torch` 中 `dataloader.worker`数量等多进程指标

```bash
#SBATCH --cpus-per-task=4
```

### 3.6 `--mem-per-cpu=` 为cpu分配内存

同样的，这一项命令推荐默认设置即可，如果真的遇到cpu内存问题可以考虑增大

```bash
#SBATCH --mem-per-cpu=2G
```


## 4. 如何在具体的Slurm任务提交过程中使用conda 

### 4.1 使用显式初始化 明确指定conda环境

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate myenv
```

### 4.2 包含上述所有内容的完整示例

这是我在学院集群中提交的一个`run.slurm`文件，由于学院集群有同时提交job数量(8次)与gpu占用数量(12张)的要求，所以我在这边使用了一个job申请两张gpu,并将其分别分配给两个单独的.sh文件

```bash
#!/bin/bash
#SBATCH --job-name=2G_J217
#SBATCH --partition=gpu          # 指定 GPU 队列或分区
#SBATCH --output=output_%j.out   # 标准输出将写入该文件，%j 代表作业ID
#SBATCH --error=error_%j.err     # 标准错误输出将写入该文件
#SBATCH --gres=gpu:2             # 请求 2 个 GPU
#SBATCH --time=4-23:00:00        # 最大运行时间 4 天
#SBATCH -n 2                     # 这个参数指定作业所需的总任务数是 2
#SBATCH --cpus-per-task=4        # 每个任务请求 4 个 CPU 核心
#SBATCH --mem-per-cpu=2G         # 每个 CPU 分配 2G 内存（根据需要调整）

source /public/home/zhouyk12023/anaconda3/etc/profile.d/conda.sh
conda activate llama31_gcg

srun --gres=gpu:1 --ntasks=1 bash ./launch_217.sh &
srun --gres=gpu:1 --ntasks=1 bash ./launch_218.sh &
wait
```


## 5. 其余有用的命令

在提交任务时，可以高效利用以下命令

```bash
watch -n 1 squeue -l            # 每秒刷新一次，监控提交作业的进度
scancel <jobid>                 # 取消作业
scontrol show job <jobid>       # 查 PENDING 原因/资源分配
```
