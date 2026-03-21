# Criterion for Flow-Control Safety Pipeline

## 1. 目标

本文档定义当前流程控制型安全 pipeline 的判断标准，用于：

- 约束运行时分流行为；
- 指导 SFT 数据构造；
- 为论文中的 decision policy 提供可复用描述。

当前框架不再让模型在每个节点都做一次完整五分类，而是采用固定流程：

1. `memory_for_plan`（纯检索，无参数）
2. `predict_risk`（带 step + 风险判断）
4. 若 `safe`：
   - `memory_for_tool`
   - hit -> 真实工具调用
   - miss -> `tool_try`
   - `judge_try_result`
   - `safe ->` 真实工具调用
   - `unsafe -> replan / ask_human / terminate`
5. 若 `risky`：
   - `replan / ask_human / refuse`

## 2. 基本原则

### 2.1 最小 step 原则

thinking 模型只形成“当前最小可执行的一个 step”，而不是完整计划。

判断要求：

- step 必须足够具体，能够映射到一个真实工具调用。
- step 不能隐含多个无法观察边界的副作用。
- 如果关键信息不足，允许不形成 step。

### 2.2 证据先于动作原则

所有关键分流都必须基于可解释证据：

- 用户明确给出的目标、范围、授权；
- 当前 step 的工具与参数；
- `memory_for_plan` 的相关经验召回；
- `tool_try` 的前后状态差异。

### 2.3 风险来源分解原则

遇到风险时，首先判断风险来自哪一类原因：

- 信息缺失
- 方案过猛或不够细
- 恶意或不允许的目标
- try 暴露出的范围外副作用

不同原因对应不同分支，不应混用。

## 3. `predict_risk` Criterion

`predict_risk` 的主输出是：

- `safe`
- `risky`

同时必须给出：

- `reasoning`
- `likely_next_action`
- `criterion_hits`

### 3.1 判为 `safe` 的条件

当以下条件大体满足时，应判为 `safe`：

- 当前 step 的目标对象基本唯一或边界基本明确。
- 风险仍然存在，但可以通过 `memory_for_tool` 或 `tool_try` 继续验证。
- 当前 step 不依赖额外用户确认即可进入工具路径。
- 风险根源不在于任务本身恶意，也不在于关键信息缺失。

典型例子：

- 删除一个用户明确指定的绝对路径文件。
- 读取一个用户明确指定的日志文件。
- 列出一个明确目录的文件列表。
- **GitLab**: `list_projects`、`list_branches`、`read_repo_file` — 纯只读查询，无副作用。
- **GitLab**: `delete_branch` 指定了明确的项目和分支名（如 `sotopia/feature-test`）— 对象唯一，可通过 try 验证。

### 3.2 判为 `risky` 的条件

当以下任一条件成立时，应判为 `risky`：

- 当前 step 的目标对象不唯一。
- 当前 step 缺少关键路径、范围、确认或授权。
- 当前 step 本身方案过猛，存在明显更温和的替代路径。
- 当前任务目标本身具有恶意、外传、破坏、窃取或越权意图。
- **GitLab**: 批量删分支但未指定具体范围（如"清理旧分支"）— 信息缺失。
- **GitLab**: `update_branch_protection(action=unprotect)` 针对所有项目 main 分支 — 越权且方案过猛。
- **GitLab**: 删除所有仓库 — 本质恶意/破坏性任务。

### 3.3 `risky` 下的后续倾向

`predict_risk` 不直接执行最终动作，但应输出最可能的下一步：

- `likely_next_action = ask_human`
  - 当风险根源是用户信息缺失。
- `likely_next_action = replan`
  - 当风险根源是当前方案不够好，但 agent 自己能提出替代方案。
- `likely_next_action = refuse`
  - 当任务目标本身不允许执行。

## 4. `ask_human` Criterion

### 4.1 应选择 `ask_human` 的情况

当继续安全完成任务必须依赖只有用户才能提供的信息时，应选择 `ask_human`。

典型条件：

- 目标对象不唯一。
- 用户没有给出完整路径、明确范围或唯一标识。
- 是否继续执行需要用户确认。
- try 暴露出多个可能安全路径，但需要用户裁决保留/删除哪一部分。

### 4.2 不应选择 `ask_human` 的情况

以下情况不应优先 ask_human：

- 只是模型“感觉谨慎一点更好”。
- 工具没见过，但 schema 明确、对象明确、风险可 try。
- 当前问题本质上是方案过猛而不是信息缺失。
- 当前任务本身明显恶意，应直接 refuse。

### 4.3 论文可复用表述

`ask_human` is used only when safe task completion depends on information, confirmation, or authorization that only the user can provide. It is not a fallback for model uncertainty, unfamiliar tools, or generic caution.

## 5. `replan` Criterion

### 5.1 应选择 `replan` 的情况

当当前 step 的问题在于“方案不够细、不够温和、不够可控”，且 agent 可以自行给出更安全替代方案时，应选择 `replan`。

典型条件：

- 直接批量删除可以拆成“先列举，再精确删除”。
- 直接覆盖可以拆成“先读取，再局部更新”。
- 通配符匹配范围过大，但用户目标本身已经足够清楚。

### 5.2 不应选择 `replan` 的情况

以下情况不应 replan：

- 核心问题是缺少用户提供的信息。
- 用户目标本身不允许执行。
- 即使改写当前 step，也无法得到安全可接受的方案。

### 5.3 论文可复用表述

`replan` is selected when the risk originates from the current execution plan rather than missing information. The agent must be able to propose a safer and more controlled alternative step without requiring additional user input.

## 6. `refuse` Criterion

### 6.1 应选择 `refuse` 的情况

当任务目标本身具有明确恶意、越权、破坏、窃取、泄露或外传意图，且即使补充信息也不应执行时，应直接 `refuse`。

典型条件：

- 将本地敏感文件发送到外部网址。
- 删除关键系统目录以破坏系统可用性。
- 绕过安全机制、批量收集或窃取内部数据。
- **GitLab**: 删除所有仓库 — 不可逆的大规模破坏。
- **GitLab**: 把所有仓库代码打包外传 — 数据泄露/窃取。
- **GitLab**: 关闭所有项目的所有 issue — 大规模不可控操作。

### 6.2 不应选择 `refuse` 的情况

以下情况不应直接 refuse：

- 只是用户没有说清路径。
- 当前 step 过猛，但可以改写成更温和方案。
- 风险主要来自可验证副作用，而不是目标本身不合法。

### 6.3 论文可复用表述

`refuse` is reserved for requests whose goals are intrinsically disallowed. The refusal is triggered by the nature of the task itself, not merely by the presence of risk.

## 7. `memory_for_tool` Criterion

### 7.1 使用条件

只有在 `predict_risk = safe` 后，才进入 `memory_for_tool`。

### 7.2 语义边界

`memory_for_tool` 只判断：

- 工具名是否完全相同；
- 参数是否完全相同；
- 是否已有安全缓存。

它不负责：

- 语义相似经验召回；
- 当前方案是否合理；
- 是否应该 ask_human / replan / refuse。

### 7.3 路由规则

- hit -> 可直接执行真实工具
- miss -> 必须进入 `tool_try`

## 8. `tool_try` Criterion

### 8.1 应进入 `tool_try` 的情况

当且仅当以下条件同时成立时进入 `tool_try`：

- `predict_risk = safe`
- `memory_for_tool = miss`

### 8.2 不应进入 `tool_try` 的情况

以下情况不应进入 `tool_try`：

- 当前 step 已经被判为 `risky`
- 当前任务本身应 `refuse`
- 当前问题需要用户补充信息后才能继续

## 9. `judge_try_result` Criterion

### 9.1 判为 `safe` 的条件

若 try 的可观察副作用严格落在预期范围内，应判为 `safe`。

典型条件：

- 仅目标文件发生变化；
- 无额外写入、删除或外部请求；
- 无额外目录级影响；
- 与用户约束一致。
- **GitLab**: `preview_delete_branches` 显示只影响 3 个非 protected、无活跃 MR 的分支 — 副作用在预期范围内。

### 9.2 判为 `unsafe` 的条件

若 try 结果出现任何范围外或不可解释变化，应判为 `unsafe`。

典型条件：

- 额外删除了不应影响的文件；
- 通配符误命中了 backup 文件；
- 发生了外部网络交互；
- 产生额外写入、权限变化或无法解释的副作用。
- **GitLab**: `preview_delete_branches` 命中 protected branch 或 default branch — 范围外副作用。
- **GitLab**: `preview_delete_branches` 命中有活跃 MR 的分支 — 可能影响进行中的代码审查。

### 9.3 `unsafe` 后的后续分流

`unsafe` 后继续分成三类：

- `replan`
  - 用户目标本身已清晰，agent 可以自行改写成更精确方案。
- `ask_human`
  - 是否继续需要用户明确保留/删除边界或补充偏好。
- `terminate`
  - 当前任务无法在安全边界内继续推进。

## 10. 真实工具直接执行 Criterion

允许直接调用真实工具的只有两类情况：

- `memory_for_tool` 命中完全相同的安全缓存；
- `tool_try` 被 `judge_try_result` 判定为 `safe`。

除此之外，不应直接执行真实副作用工具。

## 11. Completion Criterion

当 step 队列为空时，系统进入 completion 判断：

- `done`
  - 当前结果已经足以交付给用户。
- `ask_human`
  - 当前结果只是阶段性进展，用户还需要指定下一步。

completion 不负责重新解释历史风险，只负责判断任务是否闭合。

## 12. 分支映射到 `branches.md`

本文档支持的主要分支包括：

- 无风险 + 工具缓存命中 -> 直接真实执行
- 无风险 + 工具缓存未命中 + try safe -> 真实执行
- 无风险 + try unsafe -> ask_human
- 无风险 + try unsafe -> replan
- 无风险 + try unsafe -> terminate
- risky -> replan
- risky -> ask_human
- direct refuse
- ask_human 后补充信息 -> 返回流程
- replan 后成功 -> 返回流程

## 13. 当前局限

- `memory_for_plan` 是 0 参数纯检索，step 信息由 `predict_risk` 携带。
- `memory_for_plan` 已切到 embedding + cosine 的本地持久化向量检索，但还不是独立外部向量数据库服务。
- criterion 已固定，但仍需要更多 branches 数据来覆盖边界情况。
- completion 节点仍依赖 LLM 判断，后续可继续压缩为更明确规则。
