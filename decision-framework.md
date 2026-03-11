# Decision-Driven Safety Framework

这份文档是当前讨论后的统一版本，用来替代之前几份分散的未来规划文档。

核心目标已经明确：

> 当前项目不再只是一个“固定控制流程的风险拦截器”，而是要逐步演进成一个让 agent 学会在 `act / try / replan / ask_human / refuse` 五类动作之间做选择的决策框架。

本文档只保留当前讨论后仍然成立的设计，不再保留已被放弃或暂不采用的方向。

---

## 1. 当前目标

我们现在追求的不是：

- 让系统把所有未知动作都送去 `try`
- 给所有工具预先写死风险等级和默认策略
- 单纯用 `safe / unsafe` 二分类来做控制

我们现在追求的是：

- 给定任务、plan、当前 step、历史经验和执行结果
- 让模型自己判断这一步更适合：
  - `act`
  - `try`
  - `replan`
  - `ask_human`
  - `refuse`

也就是说，真正的学习目标是：

> 在具体上下文下，什么时候应该采取哪一种动作。

---

## 2. 明确不做的事

为了让框架保持清晰，当前版本明确不做以下几件事：

### 2.1 不引入 benchmark / protection mode

目前不区分 benchmark 和 protection。

原因：

- 当前重点是先把五类动作选择跑顺
- 模式切分会引入额外复杂度
- 很多决策边界在当前阶段还没稳定，过早拆模式会干扰核心问题

后续如果确实需要，再单独加。

### 2.2 不给工具添加会泄露答案的策略型 tag

当前不采用如下这类 metadata：

- `default_policy`
- `risk_level=high/medium/low`
- “这个工具默认 ask_human / try”

原因：

- 这些信息太接近最终答案
- 会弱化模型真正的决策学习能力
- 我们希望模型先根据工具描述、上下文和历史经验自己形成判断

### 2.3 不把原始 `think` 直接当作训练 gold

当前 pipeline 会产出 `think / predict / reflect / reason`，但这些是模型的原始推理痕迹，不等于稳定的训练标签。

后续训练时更适合保留的是：

- `gold_action`
- `gold_reason`
- `bad_action_reasons`

而不是无条件把原始长推理直接监督给模型。

---

## 3. 当前认定的核心流程

未来稳定版本的最小闭环应该是：

1. `generate_plan`
2. `detect_step_risk`
3. `decide_action`
4. 路由到五类动作之一
5. 执行结果写回 memory

具体如下。

### 3.1 generate_plan

这里只做一件事：

- 生成候选步骤

它不负责：

- 直接决定最终动作
- 直接判断是否 `ask_human`
- 直接把风险和执行策略写死

因此 `plan` 的定位是：

> 候选方案，而不是最终策略。

### 3.2 detect_step_risk

这一层的职责是：

- 评估当前 step 的风险证据
- 给后面的决策层提供输入

这一步不直接决定最终动作。

它产出的字段里，当前最重要的是：

- `think`
- `predict`
- `reflect`
- `reason`

其中当前建议的职责分工是：

- `think`
  只描述事实层分析：
  当前 step 在做什么，可能影响什么，哪些上下文最关键

- `predict`
  只输出风险判断结论：
  `safety risk/unsafe` 或 `without safety risk/safe`

- `reflect`
  只描述动作倾向：
  基于上面的事实，更接近 `proceed / replan / human_help` 中哪一种，以及为什么

### 3.3 decide_action

这是整个框架最重要的一层。

这一层接收：

- 用户任务
- 当前完整 plan
- 当前 step
- 风险评估结果
- 历史经验
- 已有的完全相同 safe case

输出：

- `decision`
- `reason`
- `bad_action_reasons`
- `question`（当 `ask_human` 时）

这一步才是真正的五类动作选择。

### 3.4 五类动作的含义

#### `act`

适用于：

- 低副作用
- 只读
- 明确且局部
- 无需额外验证

#### `try`

适用于：

- 有副作用
- 但范围明确
- 且可以在沙箱中验证

这是中风险、可验证动作的核心入口。

#### `replan`

适用于：

- 当前 step 本身方案不好
- 但任务仍可通过更安全路径完成

关键点：

> `replan` 不是因为“危险就停住”，而是因为“当前方案不够好，但还有更优替代路径”。

#### `ask_human`

适用于：

- 需要确认
- 需要澄清
- 需要授权

关键点：

> `ask_human` 不是“谨慎兜底”，而是“当前确实缺少人类确认的信息”。

#### `refuse`

适用于：

- 明显越权
- 明显恶意
- 明显外泄
- 明显不可接受破坏

---

## 4. Memory 的定位

当前讨论后，memory 的作用已经发生变化。

它不再只是：

- 缓存 risky plan
- 缓存 safe tool call

而应变成：

> 记录“情境 -> 风险判断 -> 动作选择 -> 执行结果 -> 经验结论”的经验库。

### 4.1 Experience Memory

Experience Memory 应记录 step 级经验。

最小结构建议如下：

```json
{
  "task": "删除 /home/user 下所有 .log 文件",
  "step": {
    "tool": "delete_file",
    "args": {
      "path": "/home/user/*.log",
      "recursive": false
    },
    "description": "删除日志文件"
  },
  "risk_assessment": {
    "is_risky": true,
    "reason": "删除有副作用，但范围局部"
  },
  "decision": "try",
  "decision_reason": "删除有副作用，但范围明确且可验证",
  "bad_action_reasons": {
    "act": "直接执行过于激进",
    "ask_human": "任务已足够明确"
  },
  "observed_result": "删除了 3 个日志文件",
  "safety_judgment": {
    "is_safe": true,
    "reason": "仅影响目标日志文件"
  },
  "outcome": "successful_try"
}
```

### 4.2 Tool Memory

Tool Memory 只保留一个较弱但有用的角色：

- 保存“完全相同调用已被验证为 safe”的记录

它不是最终决策器，只是 `decide_action` 的输入之一。

也就是说：

- 相同调用曾经 safe，并不代表现在一定 `act`
- 它只是一个经验提示

### 4.3 Memory 的学习逻辑

我们当前认可的 learning path 是：

1. 第一次只看到工具描述和上下文
2. 自己判断风险和动作
3. 执行后观察结果
4. 把这次经验写回 memory
5. 下一次再遇到类似问题时，逐渐学会这个工具的风险面

这意味着我们更偏向：

> 从经验中长出风险控制能力

而不是：

> 先用人工 tag 把所有工具的风险答案写死

---

## 5. 当前推荐的训练样本形式

如果要围绕五类动作训练模型，最小训练样本不需要很多额外 tag。

最小版本如下：

```json
{
  "context": {
    "user_task": "...",
    "candidate_step": {
      "tool": "...",
      "args": {},
      "description": "..."
    },
    "tool_description": "...",
    "memory": [...]
  },
  "risk_assessment": {
    "is_risky": true,
    "reason": "..."
  },
  "label": {
    "gold_action": "try",
    "gold_reason": "...",
    "bad_action_reasons": {
      "act": "...",
      "ask_human": "..."
    }
  }
}
```

### 为什么当前不需要太多 tag

因为当前真正的主任务就是：

> 给定上下文，做一个带解释的五分类。

也就是说核心监督信号是：

- `gold_action`
- `gold_reason`

`bad_action_reasons` 则是一个非常有价值的补充，因为它能明确说明：

- 为什么不选其他动作

当前阶段这已经足够，不必再引入过多额外标签。

---

## 6. think / reflect / reason 的职责

当前讨论后，最合理的理解是：

### `think`

事实层分析。

它回答的是：

- 当前 step 做什么
- 潜在影响是什么
- 哪些上下文最关键

### `reflect`

动作层结论。

它回答的是：

- 基于上面的事实，更接近哪种处理倾向
- 为什么更接近这一种

例如：

```json
{
  "think": "delete_file 会删除文件，属于有副作用操作，但目标路径限定在 /home/user/*.log，范围较局部，且可以在沙箱中验证删除范围。",
  "reflect": "这不是低风险直接 act 的情况，但也没到必须 ask_human。更合适的是先 try。"
}
```

### `reason`

最终压缩后的说明。

它通常用于：

- 风险结论说明
- 决策理由说明
- 安全判断说明

如果 `think` 和 `reflect` 已经区分清楚，`reason` 就应该更短、更像摘要，而不是再重复一遍长推理。

---

## 7. 当前在 `pipeline.py` 上的落地方式

当前最适合继续实验的实现不是 LangGraph，而是原生的 `pipeline.py`。

原因：

- 逻辑更透明
- 容易打印每个阶段开始和结束
- 方便直接观察 risk / decision / memory 的变化
- 出问题时更容易调 prompt 和流程

当前 `pipeline.py` 的目标应持续保持为：

- 可读
- 可 debug
- 可打印
- 可快速试验 prompt

在它稳定之后，再迁移到 `pipeline_langchain.py` / LangGraph。

---

## 8. 推荐的下一步路线

### 第一步

继续在 `pipeline.py` 上验证五类动作是否能稳定出现：

- `act`
- `try`
- `replan`
- `ask_human`
- `refuse`

### 第二步

继续调 prompt，重点解决：

- 删除类操作不要过度升级成 `ask_human`
- `replan` 与 `ask_human` 的边界更稳定
- `think` 与 `reflect` 不再重复

### 第三步

在 memory 记录足够多后，再决定是否：

- 导出训练样本
- 做 `gold_action` / `gold_reason` 数据集
- 迁移到 LangGraph 结构化版本

---

## 9. 最终一句话总结

当前统一后的框架可以概括为：

> 先生成候选 plan，再对每个 step 做风险评估，随后显式地在 `act / try / replan / ask_human / refuse` 中做选择，并把这次选择连同结果一起写回 memory，让 agent 逐步学会风险控制，而不是只执行固定控制流。
