# 工具过多 Token 优化说明

本文档用于说明本轮“工具数量上升后 prompt / snapshot token 膨胀”的解决方式，以及这套做法为什么可以继续复用到后续 7 个服务。

目标不是减少工具能力，而是减少模型在每一轮真正需要看到的工具信息量。

---

## 1. 问题是什么

在只有少量工具时，把所有 real tool 的完整 schema 都放进 prompt 还能工作；但当单个服务扩到几十个工具、后续再扩到多服务时，会出现两个问题：

1. token 成本迅速上升
   - 每个工具都带完整 `parameters/properties/required`
   - 工具越多，单轮上下文越大

2. 选工具稳定性下降
   - 模型会在大量相似 schema 中漂移
   - 容易 hallucinate 不存在的工具名
   - 容易漏掉必填参数

所以真正的问题不是“工具太多不能加”，而是“不能再把全量工具 schema 作为常驻上下文”。

---

## 2. 解决思路

本轮采用的是三层呈现 + 按阶段注入：

1. 先给模型看“工具组摘要”
2. 再给模型看“当前最相关的一小批候选工具”
3. 只有在真正执行某个工具时，才给该工具的完整 schema

一句话概括：

> 把“全量工具常驻 prompt”改成“组级摘要 + 候选检索 + 当前工具 schema 的按需注入”。

---

## 3. 具体实现

### 3.1 工具注册层先做压缩

在 `safety_pipeline/service_tools.py` 里，对每个工具增加两类轻量元数据：

- `group`
- `short_description`

同时为每个服务维护 `_SERVICE_TOOL_GROUPS`，把几十个工具先折叠成少量业务组，例如 Gitea 的：

- `repo_info`
- `repo_content`
- `branch_ops`
- `issue_tracking`
- `pull_requests`
- `ci_cd`
- `access_control`
- `labels_and_milestones`
- `releases`
- `webhooks`

这样模型在大多数轮次首先看到的是：

- 有哪些组
- 每组用途是什么
- 每组包含多少工具

而不是直接看到几十个完整 schema。

### 3.2 检索层只返回少量候选工具

在 `safety_pipeline/tool_retrieval.py` 中新增 `ToolIndex`。

它会把每个工具的这些信息拼成检索文档：

- 工具名
- 工具名的自然语言拆分
- group
- group description
- short description
- full description

检索优先级是：

1. 本地 embedding + FAISS
2. 缺依赖时退化到关键词重叠匹配
3. 如果还取不到，再做每组至少取一个的默认候选回退

运行时默认只返回 `top_k=10` 个 `candidate_tools`，而不是把所有工具展开给模型。

### 3.3 检索 query 不只看用户原话

在 `safety_pipeline/runtime.py` 中，检索 query 不是只用用户 task，而是组合这些上下文：

- 当前用户任务
- 当前 step description
- 当前 step 已有的 tool / args
- known context
- 最近两条 real tool 结果
- plan memory 里历史轨迹出现过的工具

这样做的目的不是“更智能”，而是尽量减少宽泛 task 下的候选漂移。

### 3.4 Snapshot 按 phase 分层注入

在 `build_agent_state_snapshot()` 中，三层信息的注入规则是：

1. `tool_groups`
   - 始终注入
   - 成本低
   - 用于帮助模型先确定领域

2. `candidate_tools`
   - 只在选工具阶段注入
   - 当前实现阶段：`need_risk`、`need_step`、`need_next_or_done`
   - 默认只给 10 个候选

3. `current_tool_schema`
   - 只在执行阶段注入
   - 当前实现阶段：`need_try`、`need_real_tool`
   - 一次只给当前这一个真实工具的完整 schema

这一步是 token 下降最关键的地方。

### 3.5 参数报错时只补当前工具 schema

以前如果模型参数写错，最容易发生两种浪费：

1. 再把所有工具重新展开给模型
2. 模型在大工具池里重新漂移，继续选错

现在 runtime 做法是：

- 校验当前 step 的 args
- 如果缺字段或多字段，直接把该工具的正确 schema 拼进错误信息
- 模型基于 `last_tool_error` 修当前工具调用

也就是说，错误恢复是“局部修复”，不是“重新把整个工具世界再讲一遍”。

---

## 4. 改造前后有什么变化

### 改造前

- 大量 real tool schema 常驻在上下文里
- 工具越多，prompt 越长
- 模型选工具时要在全量工具中扫描
- 参数错了以后，恢复成本高

### 改造后

- 模型常驻看到的是轻量 `tool_groups`
- 选工具阶段只看 `candidate_tools`
- 真执行时才看 `current_tool_schema`
- 参数错了只针对当前工具补 schema hint

因此 token 成本从“按全量工具数线性常驻增长”，变成了：

- 常驻部分：组摘要
- 阶段性部分：少量候选工具
- 单工具部分：当前 schema

---

## 5. 这套方案为什么有效

它解决的不是“模型能力不够”，而是“上下文组织方式不对”。

核心收益有三个：

1. 减少无效 token
   - 大部分轮次并不需要知道所有工具的完整参数

2. 降低工具间干扰
   - 模型只在小候选集里选工具，漂移概率更低

3. 缩小错误修复范围
   - 一次参数错误只需要修一个工具，而不是重扫全量工具池

---

## 6. 当前还没完全解决的点

这套方案能明显缓解 token 和漂移问题，但不是万能的。

仍然存在的典型情况：

1. 宽任务仍可能不稳定
   - 例如“先列仓库，再看设置，再看目录，再看 tag”这类 overview 任务
   - 即使有候选检索，模型也可能第一步选错

2. 风险判断问题不是 token 问题
   - 例如 `add_collaborator` 被模型判成 `risky`
   - 这更像 prompt policy / task wording 问题，不是工具注入层能单独解决的

3. fallback 目前不是严格 BM25
   - 当前降级策略是关键词重叠匹配
   - 可用，但不是最强检索基线

因此：

- 这套方案解决的是“工具规模带来的上下文膨胀”
- 不是“所有任务稳定性问题”的唯一解

---

## 7. 后续 7 个服务怎么复用

后续每个服务继续扩工具时，建议严格遵守下面几点：

1. 每个工具都必须补 `group` 和 `short_description`
   - 不要只写长 description

2. 每个服务先设计好 `8-12` 个工具组
   - 按业务领域分组
   - 不要按 HTTP endpoint 生硬分组

3. 不要把“临时为了方便调试”变成“运行时全量注入”
   - 运行时仍然应以 `tool_groups` / `candidate_tools` / `current_tool_schema` 为主

4. 新服务工具越多，越要把任务文案写窄
   - 宽任务更容易让候选检索失焦

5. 写工具新增后，要同步看 schema 是否足够精确
   - 因为执行阶段只靠当前工具 schema 兜底

---

## 8. 本轮代码落点

本次 token 优化相关改动主要落在这三个文件：

- `safety_pipeline/service_tools.py`
  - 工具分组与轻量摘要元数据

- `safety_pipeline/tool_retrieval.py`
  - 候选工具检索

- `safety_pipeline/runtime.py`
  - 三层 snapshot 注入
  - phase 感知的工具呈现
  - 参数校验错误附 schema hint

如果后续服务继续扩工具，优先复用这三层结构，而不是回到“把所有工具 schema 一次性塞给模型”的旧方式。
