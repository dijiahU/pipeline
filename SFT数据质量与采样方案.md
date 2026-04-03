# SFT 数据质量过滤与多样性采样方案

本文档描述从 experience_memory 导出可用于微调的高质量 SFT 数据集的完整流程，包括质量过滤、评测关联、路径分桶和均衡采样。

---

## 0. 现状与问题

### 数据规模

| 指标 | 当前值 |
|---|---|
| experience_memory cases | 54 |
| sft_dataset.jsonl（session 级） | 30 条 |
| sft_dataset_stepwise.jsonl（step 级） | 35 条 |
| 任务 YAML 总数 | 160 |
| 已覆盖服务 | gitea / rocketchat / owncloud / nocodb / openemr / erpnext / discourse / zammad |

### 质量问题

1. **错误重试残留**：35 条 stepwise 样本中 16 条（46%）包含 `accepted: false` 的错误调用。典型场景：模型第一次 `predict_risk` 猜错工具名或漏了必填字段，系统纠正后重试成功。这些失败调用被原样导出，会教模型"先犯错再改正"。

2. **无评测关联**：experience_memory 不记录该 session 是否通过了三项评测（decision_check / outcome_check / behavior_check）。导出时无法过滤掉评测失败的轨迹。

3. **路径不均衡**：

| preferred_action | 任务数 | 占比 |
|---|---|---|
| execute | 115 | 72% |
| refuse | 25 | 16% |
| ask_human | 15 | 9% |
| replan | 4 | 2.5% |
| terminate | 1 | 0.6% |

微调后模型会偏向高频的 execute 路径，对 replan / terminate 等安全关键路径的学习不足。

---

## 1. 质量过滤

### 1.1 Level 1: 结构完整性

丢弃不构成有效训练样本的 case。

```python
def filter_structural(cases):
    """结构完整性过滤"""
    valid = []
    for case in cases:
        # 必须有 task 和 decision
        if not case.get("task") or not case.get("decision"):
            continue
        # abort 且不是显式 refuse/terminate 的丢弃
        if case.get("status") == "aborted" and case.get("decision") not in ("refuse", "terminate"):
            continue
        # flow_tool_calls 不能为空
        if not case.get("flow_tool_calls"):
            continue
        valid.append(case)
    return valid
```

### 1.2 Level 2: 轨迹清洗（收益最大）

从 `flow_tool_calls` 中删除错误重试，只保留最终成功的调用链。

**原始轨迹（有错误重试）**:
```
predict_risk(tool=get_ci_logs)        → accepted: false, 未知工具
predict_risk(tool=get_pipeline_log)   → accepted: false, 缺少 project_id
predict_risk(tool=get_latest_pipeline_log, args={project_id: ...}) → accepted: true
tool_try(...)                         → 执行成功
judge_try_result(safe)                → accepted: true
```

**清洗后**:
```
predict_risk(tool=get_latest_pipeline_log, args={project_id: ...}) → accepted: true
tool_try(...)                         → 执行成功
judge_try_result(safe)                → accepted: true
```

```python
def clean_flow_tool_calls(flow_tool_calls):
    """清除错误重试，只保留成功的调用链。

    策略：对同一 tool_name 的连续调用组，只保留最后一个 accepted=true 的。
    如果一组内全部失败，则整组丢弃。
    """
    cleaned = []
    i = 0
    while i < len(flow_tool_calls):
        call = flow_tool_calls[i]
        tool_name = call.get("tool_name", "")
        result = call.get("result", {})

        # 判断是否为失败调用
        is_failure = (
            isinstance(result, dict)
            and result.get("accepted") is False
        )

        if not is_failure:
            # 成功调用，直接保留
            cleaned.append(call)
            i += 1
            continue

        # 当前调用失败，向前扫描同名连续调用，找最后一个成功的
        group_end = i + 1
        while group_end < len(flow_tool_calls):
            next_call = flow_tool_calls[group_end]
            if next_call.get("tool_name") != tool_name:
                break
            group_end += 1

        # 在 [i, group_end) 区间内找最后一个成功的
        last_success = None
        for j in range(i, group_end):
            r = flow_tool_calls[j].get("result", {})
            if not (isinstance(r, dict) and r.get("accepted") is False):
                last_success = flow_tool_calls[j]

        if last_success is not None:
            cleaned.append(last_success)
        # else: 整组全部失败，丢弃

        i = group_end

    return cleaned
```

清洗应同时作用于：
- `build_conversations()` 导出 session 级样本时
- `experience_step_to_sft_record()` 导出 step 级样本时

实现方式：在 `build_export_flow_tool_calls(case)` 返回前插入 `clean_flow_tool_calls()`。

### 1.3 Level 3: 评测结果关联

让每条 experience 带上评测通过/失败标记，导出时只保留通过的。

#### 1.3.1 评测结果回写

在 `evaluation.py` 的 `run_evaluation()` 中，pipeline 跑完后将 eval 结果注入 experience_memory：

```python
def run_evaluation(task_file_path):
    # ... 现有逻辑 ...
    pipeline_result = run_pipeline(task_config["task"], npc_scenario=npc)
    eval_result = TaskEvaluator(task_config, backend=backend).evaluate(pipeline_result)

    # ---- 新增：回写评测结果 ----
    tag_experience_with_eval(
        task=task_config["task"],
        eval_passed=eval_result["passed"],
        eval_checks=eval_result["checks"],
    )

    return eval_result
```

```python
def tag_experience_with_eval(task, eval_passed, eval_checks):
    """将评测结果回写到 experience_memory 中最近一个匹配的 session。

    匹配逻辑：task 文本相同的最后一组连续 cases。
    """
    cases = experience_memory.cases
    # 从后向前找匹配的 session
    matched_indices = []
    for i in range(len(cases) - 1, -1, -1):
        if cases[i].get("task") == task:
            matched_indices.append(i)
        elif matched_indices:
            break  # session 边界

    for i in matched_indices:
        cases[i]["eval_passed"] = eval_passed
        cases[i]["eval_checks"] = {
            c["check"]: c["passed"] for c in eval_checks
        }

    experience_memory.save()
```

#### 1.3.2 导出时过滤

```python
def filter_eval_passed(sessions):
    """只保留评测全部通过的 session"""
    passed = []
    for session_cases in sessions:
        # session 内任意一个 case 有 eval_passed 标记即可判断
        eval_flag = None
        for case in session_cases:
            if "eval_passed" in case:
                eval_flag = case["eval_passed"]
                break

        if eval_flag is None:
            # 没有评测标记的旧数据：保守丢弃，或标记为 unverified
            continue
        if eval_flag:
            passed.append(session_cases)

    return passed
```

### 1.4 Level 4: 语义一致性

过滤掉"碰巧对了但推理过程有问题"的样本。

```python
def filter_semantic_consistency(session_cases, oracle):
    """检查轨迹中的判断与 oracle 是否语义一致"""
    issues = []

    for case in session_cases:
        for call in (case.get("flow_tool_calls") or []):
            if call.get("tool_name") != "predict_risk":
                continue
            args = call.get("arguments", {})
            risk_result = args.get("result")
            tool_name = args.get("tool")

            # 检查 1: safe 判断用在了写工具上，但 oracle 期望 refuse
            if risk_result == "safe" and oracle.get("preferred_action") == "refuse":
                issues.append(f"predict_risk 判 safe 但 oracle 期望 refuse")

            # 检查 2: 工具名与 oracle 不匹配（如果 oracle 指定了）
            expected_tool = oracle.get("expected_tool")
            if expected_tool and tool_name and tool_name != expected_tool:
                issues.append(f"工具 {tool_name} 与 oracle 期望的 {expected_tool} 不一致")

    return len(issues) == 0, issues
```

这一层是可选的，需要 task YAML 中 oracle 配置较完善才有意义。优先级低于 Level 2 和 Level 3。

### 1.5 去重

同一个 task 多次运行会产生重复 session。按 `(task, case_type)` 去重，每组只保留最近一次评测通过的。

```python
def deduplicate_sessions(sessions):
    """按 (task, case_type) 去重，保留最新的"""
    seen = {}
    for session in sessions:
        task = session[0].get("task", "")
        case_type = _derive_case_type(session)
        key = (task, case_type)
        # 后出现的覆盖先出现的（因为 experience_memory 是 append-only）
        seen[key] = session
    return list(seen.values())
```

---

## 2. 多样性均衡采样

### 2.1 分桶策略

按决策路径类型分成 7 个桶：

| 桶 ID | 路径模式 | 对应 case_type |
|---|---|---|
| `safe_memory` | predict_risk(safe) → memory_hit → act | `safe_memory_hit` |
| `safe_try` | predict_risk(safe) → try → judge(safe) → commit | `safe_try_commit` |
| `refuse` | refuse / predict_risk(risky) → refuse | `refuse`, `ask_human_then_refuse` |
| `ask_human` | predict_risk → ask_human → 用户回复 → 继续 | `ask_human` |
| `replan` | predict_risk → replan → 新 step → execute | 包含 `replan` 的 |
| `terminate` | try → judge(unsafe) → terminate | `terminate` |
| `multi_step` | 任何包含 2+ 步的完整执行 | `total_steps > 1` |

```python
BUCKET_RULES = [
    # (桶名, 匹配函数)
    ("terminate",   lambda m: "terminate" in m["case_type"]),
    ("replan",      lambda m: "replan" in m["case_type"]),
    ("ask_human",   lambda m: m["case_type"] == "ask_human"
                              or m["case_type"].startswith("ask_human_then_")
                              and "refuse" not in m["case_type"]),
    ("refuse",      lambda m: "refuse" in m["case_type"]),
    ("multi_step",  lambda m: m["total_steps"] > 1
                              and "refuse" not in m["case_type"]),
    ("safe_memory", lambda m: "safe_memory_hit" in m["case_type"]),
    ("safe_try",    lambda m: "safe_try" in m["case_type"]),
]

def assign_bucket(meta):
    """按优先级匹配第一个命中的桶"""
    for bucket_name, match_fn in BUCKET_RULES:
        if match_fn(meta):
            return bucket_name
    return "safe_try"  # 兜底
```

匹配优先级：稀有路径优先匹配，避免被 safe_try 等大桶吞掉。

### 2.2 桶间均衡

```python
def balanced_sample(records, target_total=None, min_per_bucket=5):
    """桶间均衡采样

    Args:
        records: 过滤后的 SFT 记录列表，每条包含 meta 字段
        target_total: 目标总样本数，None 表示不做下采样上限
        min_per_bucket: 每个桶的最低样本数
    """
    # 分桶
    buckets = defaultdict(list)
    for record in records:
        bucket = assign_bucket(record["meta"])
        buckets[bucket].append(record)

    # 统计
    print("=== 分桶统计 ===")
    for name in [b[0] for b in BUCKET_RULES]:
        print(f"  {name}: {len(buckets.get(name, []))}")

    # 计算配额
    n_buckets = len([b for b in buckets if buckets[b]])
    if target_total:
        base_quota = target_total // n_buckets
    else:
        base_quota = max(len(b) for b in buckets.values()) if buckets else 10

    sampled = []
    augment_needed = {}

    for bucket_name, bucket_records in buckets.items():
        n = len(bucket_records)
        quota = max(base_quota, min_per_bucket)

        if n >= quota:
            # 下采样：用桶内多样性采样
            sampled.extend(diverse_subsample(bucket_records, quota))
        elif n >= min_per_bucket:
            # 数量够最低线，全量保留
            sampled.extend(bucket_records)
        else:
            # 数量不足，全量保留 + 记录需要上采样的差额
            sampled.extend(bucket_records)
            augment_needed[bucket_name] = min_per_bucket - n

    return sampled, augment_needed
```

### 2.3 桶内多样性采样

同一个桶内可能有很多相似的任务（如 10 个仓库的 `list_projects`）。下采样时保证任务和工具的多样性。

```python
def diverse_subsample(records, quota):
    """桶内多样性采样：按 gold_path 模式轮转选取

    同一种 gold_path 不会连续占满配额，每种路径轮流取一条。
    """
    # 按 gold_path 分组
    by_path = defaultdict(list)
    for r in records:
        path_key = tuple(r["meta"]["gold_path"])
        by_path[path_key].append(r)

    # 每组内部按 task 文本排序，确保不同 task 优先
    for group in by_path.values():
        # 用 task 文本 hash 打乱，避免按文件名排序导致的偏差
        group.sort(key=lambda r: hash(r["meta"]["task"]))

    # 轮转选取
    result = []
    groups = list(by_path.values())
    while len(result) < quota and groups:
        next_groups = []
        for group in groups:
            if len(result) >= quota:
                break
            if group:
                result.append(group.pop(0))
                if group:
                    next_groups.append(group)
        groups = next_groups

    return result
```

### 2.4 稀有路径上采样

对于样本不足 `min_per_bucket` 的桶（主要是 replan 和 terminate），有两个策略：

#### 策略 A: 任务级扩充（优先，人工完成）

为稀有路径编写更多 task YAML。每个稀有路径至少覆盖 3 个不同服务：

**replan 场景模板**:
```yaml
id: {service}-{operation}-replan
task: "对 {target} 执行 {vague_action}。"
oracle:
  preferred_action: replan
  expected_flow:
    - predict_risk
    - replan
    - predict_risk
    - execute
scenarios:
  name: "{npc_name}"
  role: "项目成员"
  public_info: "了解项目结构"
```

典型 replan 触发场景：
- 用户描述模糊，agent 选了错的工具 → replan 换另一个
- 第一个工具的前置条件不满足 → replan 用替代方案
- agent 试图用写工具但判 risky → replan 成只读查看

**terminate 场景模板**:
```yaml
id: {service}-{operation}-terminate
task: "{dangerous_action_that_passes_risk_but_fails_try}"
oracle:
  preferred_action: terminate
  expected_flow:
    - predict_risk
    - tool_try
    - judge_try_result:unsafe
    - terminate
```

典型 terminate 触发场景：
- 操作执行后发现影响范围超出预期 → 回滚 + 终止
- try 结果显示已经造成了不可预料的副作用
- 外部系统返回了意外的错误状态

#### 策略 B: 样本级参数替换（自动完成）

对现有样本做参数层面的变体，保持决策路径不变：

```python
def augment_by_param_swap(record, n_variants):
    """对现有样本做参数替换生成变体

    替换 conversations 中的具体参数（仓库名、分支名、issue 编号等），
    保持 flow tool 调用序列和决策路径不变。
    """
    variants = []

    # 参数替换池
    REPO_NAMES = ["alpha-project", "beta-service", "gamma-lib", "delta-api"]
    BRANCH_NAMES = ["feature-auth", "hotfix-db", "release-v2", "test-ci"]
    ISSUE_IDS = ["5", "12", "27", "3"]

    original_text = json.dumps(record["conversations"], ensure_ascii=False)

    for i in range(n_variants):
        new_text = original_text
        # 替换仓库名
        for old_repo in re.findall(r'"(?:project_id|repo)"\s*:\s*"([^"]+)"', original_text):
            new_text = new_text.replace(old_repo, REPO_NAMES[i % len(REPO_NAMES)])
        # ... 类似替换分支名、issue 编号等

        variant = copy.deepcopy(record)
        variant["conversations"] = json.loads(new_text)
        variant["meta"]["augmented"] = True
        variant["meta"]["source_task"] = record["meta"]["task"]
        variants.append(variant)

    return variants
```

参数替换上采样是辅助手段，优先用策略 A 扩充真实任务。替换生成的样本在 meta 中标记 `augmented: true`，方便后续分析。

---

## 3. 完整导出流程

```
experience_memory.json
        │
        ▼
  group_experience_cases()         # 按 session 分组
        │
        ▼
  filter_structural()              # Level 1: 丢弃残缺 case
        │
        ▼
  clean_flow_tool_calls()          # Level 2: 清除错误重试
        │
        ▼
  filter_eval_passed()             # Level 3: 只保留评测通过的
        │
        ▼
  filter_semantic_consistency()    # Level 4: 语义一致性（可选）
        │
        ▼
  deduplicate_sessions()           # 去重
        │
        ▼
  export to records                # 转成 SFT record 格式
        │
        ▼
  assign_bucket() for each record  # 按路径类型分桶
        │
        ▼
  balanced_sample()                # 桶间均衡
   ├─ diverse_subsample()          # 桶内多样性下采样
   └─ augment_by_param_swap()      # 稀有路径上采样
        │
        ▼
  sft_dataset_balanced.jsonl       # 最终训练数据
```

### 新增入口命令

```bash
# 导出带质量过滤的数据集（不做均衡采样）
python -m safety_pipeline --export-sft --filtered

# 导出带质量过滤 + 均衡采样的数据集
python -m safety_pipeline --export-sft --balanced --target-total 200

# 查看数据集统计信息（分桶分布、质量指标）
python -m safety_pipeline --export-sft --stats-only
```

---

## 4. 代码组织

### 新增文件

```
safety_pipeline/
  sft_filter.py        # Level 1-4 过滤 + 去重
  sft_sampler.py       # 分桶 + 均衡采样 + 上采样
```

### 修改文件

| 文件 | 改动 |
|---|---|
| `runtime.py` | `build_export_flow_tool_calls()` 返回前接入 `clean_flow_tool_calls()` |
| `runtime.py` | 新增 `export_balanced_to_jsonl()` 调用 filter + sampler |
| `evaluation.py` | `run_evaluation()` 结束后调用 `tag_experience_with_eval()` |
| `__main__.py` | 新增 `--export-sft` 命令行参数 |

### 依赖

无新依赖。所有过滤和采样逻辑用纯 Python 标准库实现。

---

## 5. 实施顺序

| 阶段 | 内容 | 产出 |
|---|---|---|
| **Phase 1** | `sft_filter.py`: Level 1 + Level 2（结构过滤 + 轨迹清洗） | 立即可用，消除 46% 的脏数据 |
| **Phase 2** | `evaluation.py` 改造：评测结果回写 experience_memory | Level 3 过滤的前置条件 |
| **Phase 3** | `sft_filter.py`: Level 3（评测关联过滤）+ 去重 | 只导出验证通过的样本 |
| **Phase 4** | `sft_sampler.py`: 分桶 + 均衡采样 + 桶内多样性 | 均衡数据集 |
| **Phase 5** | 为 replan / terminate 补充 task YAML（每类至少 10 个） | 稀有路径数据量 |
| **Phase 6** | `sft_sampler.py`: 参数替换上采样 | 进一步补充稀有桶 |
| **Phase 7** | Level 4 语义一致性（可选） | 精细质量把控 |

Phase 1-3 是核心，解决"数据不能用"的问题。Phase 4-5 解决"数据不均衡"的问题。Phase 6-7 是锦上添花。

---

## 6. 质量验收指标

导出数据集后，运行 `--stats-only` 应满足：

| 指标 | 目标 |
|---|---|
| 包含 error observation 的样本 | 0% |
| 每条样本 eval_passed | 100%（或标记 unverified） |
| 重复 (task, case_type) | 0 |
| 每个桶样本数 | >= min_per_bucket |
| 桶间最大/最小比值 | <= 5:1 |
| 总样本数 | >= 100（首轮），目标 500+ |
