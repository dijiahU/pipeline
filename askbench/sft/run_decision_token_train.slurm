#!/bin/bash
#SBATCH --job-name=hcj_askbench_decision_sft
#SBATCH --partition=gpu
#SBATCH --output=/home/hcj/pipeline/askbench/sft/logs/%x_%j.out
#SBATCH --error=/home/hcj/pipeline/askbench/sft/logs/%x_%j.err
#SBATCH --gres=gpu:1
#SBATCH --time=2-00:00:00
#SBATCH -n 1
#SBATCH --cpus-per-task=4
#SBATCH --mem-per-cpu=2G

source ~/miniconda3/etc/profile.d/conda.sh
conda activate /home/hcj/pipeline/.conda-envs/askbench-decision-sft
export ASKBENCH_TRAIN_CONFIG="${ASKBENCH_TRAIN_CONFIG:-train_trl_decision_tokens.yaml}"
srun --gres=gpu:1 --ntasks=1 bash /home/hcj/pipeline/askbench/sft/launch_decision_token_train.sh
