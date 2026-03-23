# ZFS 容器级沙箱方案

## 1. 背景与动机

当前 pipeline 中 `tool_try` 使用 preview API 模拟执行，存在两个问题：

1. **覆盖不全：** 不是所有服务、所有操作都有对应的 preview API
2. **概念不一致：** preview 不是真正的沙箱隔离，tool_try 和 direct_tool 打的是同一个服务实例

理想方案是 **tool_try 直接执行真实操作**，如果 `judge_try_result = unsafe` 则回滚到执行前的状态。由于所有服务都运行在 Docker 容器内，容器内的一切（DB、文件系统、缓存）都可以通过 ZFS 快照实现完整回滚。

### 为什么选 ZFS

| 方案 | 回滚完整性 | 延迟 | 限制 |
|------|-----------|------|------|
| DB 事务回滚 | 不完整（文件系统/缓存回滚不了） | <1s | 只能回滚 DB |
| docker commit + 重建 | 完整 | 30s–数分钟 | 太慢 |
| 文件系统 cp/rsync | 完整 | 与数据量成正比 | 大数据集慢 |
| **ZFS 快照** | **完整** | **快照 <1ms，回滚 <100ms** | 需要 ZFS 文件系统 |

ZFS 快照基于 **写时复制（Copy-on-Write）**，创建快照只是记录一个元数据指针，不复制任何实际数据。回滚只需切换指针并释放变更块。时间复杂度与数据集大小无关，只与快照后的变更量有关。

---

## 2. 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                    Linux 服务器                           │
│                                                         │
│  ┌─────────────────────────────────────────────────┐    │
│  │              ZFS Pool (docker-pool)              │    │
│  │                                                  │    │
│  │  docker-pool/volumes/                            │    │
│  │    ├── gitlab/                                   │    │
│  │    │   ├── config    ← /etc/gitlab               │    │
│  │    │   ├── data      ← /var/opt/gitlab           │    │
│  │    │   └── logs      ← /var/log/gitlab           │    │
│  │    ├── gitea/                                    │    │
│  │    │   └── data      ← /data/gitea               │    │
│  │    ├── nocodb/                                   │    │
│  │    │   └── data      ← /usr/app/data             │    │
│  │    └── ...                                       │    │
│  └─────────────────────────────────────────────────┘    │
│                                                         │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                │
│  │  GitLab  │ │  Gitea   │ │  NocoDB  │  ...            │
│  │ Container│ │ Container│ │ Container│                  │
│  └──────────┘ └──────────┘ └──────────┘                │
│                                                         │
│  ┌─────────────────────────────────────────────────┐    │
│  │           Pipeline / GRPO Trainer                │    │
│  │                                                  │    │
│  │  tool_try 前: zfs snapshot                       │    │
│  │  judge = unsafe: zfs rollback + restart          │    │
│  │  judge = safe: 删快照，继续                       │    │
│  └─────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

---

## 3. 环境搭建

### 3.1 安装 ZFS

```bash
# Ubuntu 22.04 / 24.04
sudo apt update
sudo apt install -y zfsutils-linux

# 验证
zfs --version
# 确保 2.2.3+ (24.04) 或 2.1.x (22.04)

# 如果是 Ubuntu 24.04 且版本 < 2.2.3，需要禁用 block_cloning（有数据损坏 bug）
# sudo zfs set block_cloning=off <pool>
```

### 3.2 创建 ZFS 存储池

根据服务器情况选择一种：

**方案 A：使用空闲磁盘（推荐）**
```bash
# 使用整块磁盘，性能最好
sudo zpool create -o ashift=12 \
  -O compression=lz4 \
  -O atime=off \
  -O xattr=sa \
  docker-pool /dev/sdb
```

**方案 B：使用磁盘分区**
```bash
sudo zpool create -o ashift=12 \
  -O compression=lz4 \
  -O atime=off \
  docker-pool /dev/sda3
```

**方案 C：使用文件（开发/测试用，性能较差）**
```bash
sudo truncate -s 100G /opt/zfs-docker.img
sudo zpool create -o ashift=12 \
  -O compression=lz4 \
  -O atime=off \
  docker-pool /opt/zfs-docker.img
```

### 3.3 创建卷结构

为每个服务创建独立的 ZFS 数据集，支持独立快照和回滚：

```bash
# 顶层卷目录
sudo zfs create docker-pool/volumes

# GitLab 卷
sudo zfs create docker-pool/volumes/gitlab
sudo zfs create docker-pool/volumes/gitlab/config
sudo zfs create docker-pool/volumes/gitlab/data
sudo zfs create docker-pool/volumes/gitlab/logs

# Gitea 卷
sudo zfs create docker-pool/volumes/gitea
sudo zfs create docker-pool/volumes/gitea/data

# NocoDB 卷
sudo zfs create docker-pool/volumes/nocodb
sudo zfs create docker-pool/volumes/nocodb/data

# Plane 卷
sudo zfs create docker-pool/volumes/plane
sudo zfs create docker-pool/volumes/plane/db
sudo zfs create docker-pool/volumes/plane/storage

# Mattermost 卷
sudo zfs create docker-pool/volumes/mattermost
sudo zfs create docker-pool/volumes/mattermost/data
sudo zfs create docker-pool/volumes/mattermost/db
```

### 3.4 Docker Compose 配置

将 Docker 卷指向 ZFS 数据集的挂载点：

```yaml
# docker-compose.yml
services:
  gitlab:
    image: ghcr.io/theagentcompany/servers-gitlab:1.0.0
    ports:
      - "8929:8929"
    volumes:
      - type: bind
        source: /docker-pool/volumes/gitlab/config
        target: /etc/gitlab
      - type: bind
        source: /docker-pool/volumes/gitlab/data
        target: /var/opt/gitlab
      - type: bind
        source: /docker-pool/volumes/gitlab/logs
        target: /var/log/gitlab

  gitea:
    image: gitea/gitea:latest
    ports:
      - "3000:3000"
    volumes:
      - type: bind
        source: /docker-pool/volumes/gitea/data
        target: /data

  nocodb:
    image: nocodb/nocodb:latest
    ports:
      - "8080:8080"
    volumes:
      - type: bind
        source: /docker-pool/volumes/nocodb/data
        target: /usr/app/data
```

### 3.5 初始化并创建基准快照

```bash
# 启动所有服务
docker compose up -d

# 等待服务完全就绪（GitLab 需要 3-8 分钟）
bash scripts/setup_env.sh

# 创建基准快照（初始状态）
sudo zfs snapshot -r docker-pool/volumes/gitlab@pristine
sudo zfs snapshot -r docker-pool/volumes/gitea@pristine
sudo zfs snapshot -r docker-pool/volumes/nocodb@pristine

# 验证
zfs list -t snapshot
```

### 3.6 ZFS 内存调优

ZFS 的 ARC 缓存默认占用 50% 系统内存，需要限制以给 Docker 容器和训练留足空间：

```bash
# 查看当前 ARC 使用
cat /proc/spl/kstat/zfs/arcstats | grep c_max

# 设置 ARC 上限（根据服务器内存调整）
# 16GB 服务器建议 2-4GB，32GB 建议 4-8GB，64GB 建议 8-16GB
echo "options zfs zfs_arc_max=4294967296" | sudo tee /etc/modprobe.d/zfs.conf  # 4GB
sudo update-initramfs -u

# 临时生效（不需要重启）
echo 4294967296 | sudo tee /sys/module/zfs/parameters/zfs_arc_max
```

---

## 4. Pipeline 集成

### 4.1 核心接口

在 `EnvironmentBackend` 中新增快照/回滚方法：

```python
# safety_pipeline/environment.py

import subprocess
import time


class ZFSSnapshotManager:
    """ZFS 快照管理器"""

    def __init__(self, dataset_prefix):
        """
        Args:
            dataset_prefix: ZFS 数据集前缀，如 "docker-pool/volumes/gitlab"
        """
        self.dataset_prefix = dataset_prefix
        self._snap_counter = 0

    def _run_zfs(self, cmd):
        """执行 ZFS 命令"""
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            raise RuntimeError(f"ZFS 命令失败: {' '.join(cmd)}\n{result.stderr}")
        return result.stdout

    def snapshot(self, name=None):
        """创建快照，返回快照名"""
        if name is None:
            self._snap_counter += 1
            name = f"try-{self._snap_counter}-{int(time.time())}"
        self._run_zfs([
            "sudo", "zfs", "snapshot", "-r",
            f"{self.dataset_prefix}@{name}"
        ])
        return name

    def rollback(self, name):
        """回滚到指定快照（需要先停容器）"""
        # 获取所有子数据集
        output = self._run_zfs([
            "zfs", "list", "-r", "-H", "-o", "name",
            self.dataset_prefix
        ])
        datasets = output.strip().split("\n")

        # 逐个回滚（ZFS rollback 不递归子数据集）
        for ds in datasets:
            snap = f"{ds}@{name}"
            try:
                self._run_zfs(["sudo", "zfs", "rollback", "-r", snap])
            except RuntimeError:
                pass  # 某些子数据集可能没有这个快照

    def destroy_snapshot(self, name):
        """删除快照"""
        self._run_zfs([
            "sudo", "zfs", "destroy", "-r",
            f"{self.dataset_prefix}@{name}"
        ])

    def rollback_to_pristine(self):
        """回滚到初始基准状态"""
        self.rollback("pristine")
```

### 4.2 集成到 EnvironmentBackend

```python
class GitLabBackend(EnvironmentBackend):

    def __init__(self):
        self._gitlab_tools = None
        self._zfs = None
        self._compose_service = "gitlab"

    def _get_zfs(self):
        if self._zfs is None:
            dataset = os.environ.get(
                "ZFS_GITLAB_DATASET",
                "docker-pool/volumes/gitlab"
            )
            self._zfs = ZFSSnapshotManager(dataset)
        return self._zfs

    def snapshot_before_try(self):
        """tool_try 前创建快照"""
        return self._get_zfs().snapshot()

    def rollback_after_unsafe(self, snap_name):
        """judge_try_result = unsafe 时回滚"""
        # 1. 停容器
        subprocess.run(
            ["docker", "compose", "stop", self._compose_service],
            capture_output=True, timeout=60
        )
        # 2. 回滚
        self._get_zfs().rollback(snap_name)
        # 3. 重启容器
        subprocess.run(
            ["docker", "compose", "start", self._compose_service],
            capture_output=True, timeout=60
        )
        # 4. 等待服务就绪
        self._wait_until_ready()

    def discard_snapshot(self, snap_name):
        """judge_try_result = safe 时删除快照"""
        self._get_zfs().destroy_snapshot(snap_name)

    def run_try(self, name, args):
        """
        新 tool_try 策略：直接执行真实操作
        快照在外层（flow_tool_try）管理
        """
        gt = self._get_gitlab_tools()
        exec_result = gt.call_tool(name, args)

        # 构建观察结果
        try:
            parsed = (
                json.loads(exec_result)
                if isinstance(exec_result, str)
                else exec_result
            )
        except (json.JSONDecodeError, TypeError):
            parsed = {}

        summary = {
            "exec_status": "success",
            "state_changed": name in _GITLAB_WRITE_TOOLS,
            "affected_objects_count": self._count_affected(parsed),
            "affected_objects_sample": self._sample_affected(parsed),
            "unexpected_side_effect": False,
            "observed_effects": [f"真实执行 {name}"],
            "summary": f"{name} 已执行，结果待 judge 判定。",
        }
        return {
            "summary": summary,
            "exec_result_raw": exec_result,
        }

    def reset(self):
        """环境重置：回滚到基准快照"""
        subprocess.run(
            ["docker", "compose", "stop", self._compose_service],
            capture_output=True, timeout=60
        )
        self._get_zfs().rollback_to_pristine()
        subprocess.run(
            ["docker", "compose", "start", self._compose_service],
            capture_output=True, timeout=60
        )
        self._wait_until_ready()

    def _wait_until_ready(self):
        """轮询直到服务 API 可用"""
        import requests
        base = os.environ.get("GITLAB_BASE_URL", "http://localhost:8929")
        for _ in range(120):
            try:
                r = requests.get(f"{base}/api/v4/version", timeout=2)
                if r.status_code == 200:
                    return
            except Exception:
                pass
            time.sleep(2)
        raise RuntimeError("GitLab 启动超时")
```

### 4.3 修改 flow_tool_try

```python
# safety_pipeline/runtime.py

def flow_tool_try(state):
    """tool_try：真实执行 + ZFS 快照保护"""
    print_stage_start("flow_tool: tool_try")
    step = state["current_step"]
    backend = get_environment_backend()

    # 1. 创建快照
    snap_name = None
    if hasattr(backend, "snapshot_before_try"):
        snap_name = backend.snapshot_before_try()
        print(f"[ZFS] 快照已创建: {snap_name}")

    # 2. 直接执行真实操作
    result = backend.run_try(step["tool"], step["args"])

    # 3. 保存快照名到 state，供 judge 后决定回滚或保留
    state["current_try_snapshot"] = snap_name
    state["current_try_result"] = result["summary"]
    state["current_try_raw"] = result.get("exec_result_raw", "")

    print_json_block("tool_try_result", state["current_try_result"])
    print_stage_end("flow_tool: tool_try", "执行完成")
    return result


def flow_tool_judge_try_result(state, result, reasoning, **kwargs):
    """judge 后处理：safe 保留，unsafe 回滚"""
    # ... 原有 judge 逻辑 ...

    backend = get_environment_backend()
    snap_name = state.get("current_try_snapshot")

    if result == "safe":
        # 删除快照，保留执行结果
        # 不需要再执行 direct_tool，因为 tool_try 已经是真实执行
        if snap_name and hasattr(backend, "discard_snapshot"):
            backend.discard_snapshot(snap_name)
            print(f"[ZFS] 快照已删除: {snap_name}")
    else:
        # 回滚到执行前状态
        if snap_name and hasattr(backend, "rollback_after_unsafe"):
            print(f"[ZFS] 回滚中: {snap_name}")
            backend.rollback_after_unsafe(snap_name)
            print(f"[ZFS] 回滚完成")

    state["current_try_snapshot"] = None
    # ... 后续路由逻辑 ...
```

---

## 5. 流程变化

### 5.1 旧流程（Preview 方案）

```
predict_risk(safe)
  → memory_for_tool(miss)
  → tool_try: 调 preview API（只读模拟）
  → judge_try_result
    → safe: direct_tool（真实执行）     ← 执行了两次（preview + 真实）
    → unsafe: replan / ask_human        ← 无需回滚（没改过状态）
```

### 5.2 新流程（ZFS 沙箱方案）

```
predict_risk(safe)
  → memory_for_tool(miss)
  → [ZFS snapshot]
  → tool_try: 直接执行真实操作
  → judge_try_result
    → safe: 删快照，继续               ← 只执行了一次
    → unsafe: ZFS rollback + 重启容器   ← 完整回滚
```

### 5.3 关键区别

| 维度 | Preview 方案 | ZFS 沙箱方案 |
|------|------------|------------|
| tool_try 执行内容 | 调 preview API | 执行真实操作 |
| 覆盖范围 | 仅有 preview 的操作 | 所有操作 |
| safe 时 | 还需执行 direct_tool | 不需要，已经执行了 |
| unsafe 时 | 无需回滚 | ZFS rollback + 容器重启 |
| unsafe 回滚延迟 | 0 | 5-30s（主要是容器重启） |
| 观察结果真实性 | 模拟的 | 真实的 |

### 5.4 direct_tool 的变化

ZFS 方案下 `judge_try_result = safe` 之后 **不需要再调 direct_tool**，因为 tool_try 已经执行了真实操作。流程中的 `direct_tool` 节点可以：

- **方案 A（简化）：** 去掉 direct_tool 节点，judge = safe 后直接进入 completion_check
- **方案 B（兼容）：** 保留 direct_tool 节点，但实现为空操作（跳过），保持流程图形式不变

推荐方案 B，对外的流程描述不变，内部实现优化。

---

## 6. GRPO 训练集成

### 6.1 环境池 + ZFS

GRPO 需要 K 个并行环境实例，每个实例有独立的 ZFS 数据集：

```bash
# 为 K=8 个 GitLab 实例创建独立数据集
for i in $(seq 0 7); do
  sudo zfs create docker-pool/volumes/gitlab-$i
  sudo zfs create docker-pool/volumes/gitlab-$i/config
  sudo zfs create docker-pool/volumes/gitlab-$i/data
  sudo zfs create docker-pool/volumes/gitlab-$i/logs
done
```

### 6.2 Rollout 流程

```
对于每条轨迹：
  1. 从基准快照开始（或前一步的快照）
  2. Agent 执行 tool_try
     → 创建快照 @step-N
     → 真实执行
     → judge
       → safe: 删快照，继续 step N+1
       → unsafe: rollback 到 @step-N，记录奖励
  3. 轨迹结束后 rollback 到 @pristine 为下一条轨迹准备
```

### 6.3 并行安全性

ZFS 快照操作是 **per-dataset** 的，不同实例（gitlab-0, gitlab-1, ...）的快照/回滚互不影响，天然支持并行 rollout。

---

## 7. 各服务回滚延迟估算

回滚总延迟 = ZFS rollback (<100ms) + 容器重启 + 服务就绪

| 服务 | 容器重启 + 就绪时间 | 总回滚延迟 | 说明 |
|------|-------------------|-----------|------|
| **Gitea** | 5-15s | **~15s** | Go 单进程，启动快 |
| **NocoDB** | 5-10s | **~10s** | Node.js 单进程 |
| **Mattermost** | 10-20s | **~20s** | Go，需等 DB 就绪 |
| **BookStack** | 5-10s | **~10s** | PHP，轻量 |
| **Grafana** | 3-5s | **~5s** | Go，极快 |
| **Cal.com** | 10-20s | **~20s** | Node.js + 数据库迁移检查 |
| **Rocket.Chat** | 15-30s | **~30s** | Node.js + MongoDB |
| **Plane** | 20-40s | **~40s** | 多服务组合 |
| **Zammad** | 30-60s | **~60s** | Ruby on Rails + Elasticsearch |
| **GitLab** | 2-5min | **~3min** | 最重，多进程初始化 |
| **ERPNext** | 2-5min | **~3min** | Python + MariaDB + Redis |
| **OpenMRS** | 1-3min | **~2min** | Java/Tomcat |

### 7.1 降低回滚延迟的策略

对于重量级服务（GitLab、ERPNext），容器重启是瓶颈。优化方向：

**策略 1：不停容器，只重启服务进程**
```bash
# 不重建容器，只重启内部服务
docker exec gitlab gitlab-ctl restart
# 比 docker compose stop/start 快，但仍需等初始化
```

**策略 2：用轻量替代品训练**

| 重量级服务 | 轻量替代 | 回滚延迟降幅 |
|-----------|---------|------------|
| GitLab (3min) | Gitea (15s) | 12x |
| ERPNext (3min) | Odoo (30s) 或 Akaunting (10s) | 6-18x |
| OpenMRS (2min) | OpenEMR (30s) | 4x |

**策略 3：减少 unsafe 回滚次数**

通过 SFT 预训练让模型的 unsafe 率降低到 <10%，则大部分 tool_try 不需要回滚。

---

## 8. Mac 开发环境兼容

Mac 不支持 ZFS，开发时使用降级方案：

```python
class SnapshotStrategy:
    """快照策略抽象"""
    ZFS = "zfs"
    PREVIEW = "preview"       # Mac 降级：使用 preview API
    RESET = "reset"           # Mac 降级：unsafe 时调 reset API

def get_snapshot_strategy():
    """根据环境自动选择策略"""
    if os.path.exists("/proc/spl/kstat/zfs/arcstats"):
        return SnapshotStrategy.ZFS
    return SnapshotStrategy.PREVIEW
```

在 `EnvironmentBackend` 中根据策略分发：

```python
class GitLabBackend(EnvironmentBackend):

    def __init__(self):
        self._strategy = get_snapshot_strategy()

    def run_try(self, name, args):
        if self._strategy == SnapshotStrategy.ZFS:
            return self._run_try_real(name, args)      # 真实执行
        else:
            return self._run_try_preview(name, args)   # 降级到 preview
```

**效果：**
- Linux 服务器：ZFS 快照，tool_try 真实执行，完整回滚
- Mac 开发机：自动降级到 preview 模式，行为不变

---

## 9. 实施步骤

### Phase 1：服务器环境搭建（1-2 小时）

```bash
# 1. 安装 ZFS
sudo apt install -y zfsutils-linux

# 2. 创建存储池（根据实际磁盘调整）
sudo zpool create -o ashift=12 -O compression=lz4 -O atime=off docker-pool /dev/sdb

# 3. 创建卷结构
sudo zfs create docker-pool/volumes
sudo zfs create -p docker-pool/volumes/gitlab/config
sudo zfs create -p docker-pool/volumes/gitlab/data
sudo zfs create -p docker-pool/volumes/gitlab/logs

# 4. 调整 ARC 内存上限
echo "options zfs zfs_arc_max=4294967296" | sudo tee /etc/modprobe.d/zfs.conf

# 5. 修改 docker-compose.yml 使用 bind mount
# 6. 启动服务并创建基准快照
docker compose up -d
# 等待就绪...
sudo zfs snapshot -r docker-pool/volumes/gitlab@pristine
```

### Phase 2：代码集成（半天）

1. 实现 `ZFSSnapshotManager` 类
2. 修改 `EnvironmentBackend` 接口，新增 `snapshot_before_try()` / `rollback_after_unsafe()` / `discard_snapshot()`
3. 修改 `flow_tool_try()` 和 `flow_tool_judge_try_result()` 调用快照接口
4. 添加 `get_snapshot_strategy()` 自动检测
5. 保留 Mac 上的 preview 降级路径

### Phase 3：验证（半天）

```bash
# 测试快照/回滚
sudo zfs snapshot -r docker-pool/volumes/gitlab@test1
# 执行一个写操作（如删除分支）
docker compose stop gitlab
sudo zfs rollback -r docker-pool/volumes/gitlab/config@test1
sudo zfs rollback -r docker-pool/volumes/gitlab/data@test1
sudo zfs rollback -r docker-pool/volumes/gitlab/logs@test1
docker compose start gitlab
# 验证分支是否恢复
```

### Phase 4：GRPO 多实例扩展

按需为每个并行实例创建独立数据集，复用同一套快照管理代码。

---

## 10. 注意事项

### 10.1 Ubuntu 版本兼容

| Ubuntu | ZFS 版本 | 注意 |
|--------|---------|------|
| 22.04 | 2.1.x | 稳定，无已知问题 |
| 24.04 | 2.2.x | 确保 ≥ 2.2.3，否则禁用 `block_cloning` |

### 10.2 Docker 安装方式

必须使用 apt 安装 Docker，**不能用 snap**（snap 隔离导致看不到 ZFS 挂载点）。

### 10.3 启动顺序

ZFS pool 必须在 Docker 之前挂载，添加 systemd 依赖：

```ini
# /etc/systemd/system/docker.service.d/zfs.conf
[Unit]
After=zfs-mount.service
Requires=zfs-mount.service
```

### 10.4 sudo 权限

ZFS 命令需要 root 权限。训练脚本需要配置 sudoers 免密：

```bash
# /etc/sudoers.d/pipeline-zfs
pipeline-user ALL=(ALL) NOPASSWD: /sbin/zfs snapshot *
pipeline-user ALL=(ALL) NOPASSWD: /sbin/zfs rollback *
pipeline-user ALL=(ALL) NOPASSWD: /sbin/zfs destroy *
pipeline-user ALL=(ALL) NOPASSWD: /sbin/zfs list *
```

### 10.5 磁盘空间

快照占用空间 = 快照后被修改/删除的数据量。一般情况下：
- 单次 tool_try 的快照：几 MB 到几十 MB
- 及时删除不需要的快照即可
- 用 `zfs list -t snapshot -o name,used` 监控快照空间占用
