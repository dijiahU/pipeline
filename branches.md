# Branches for SFT Data

## 无风险主分支
### 1. 无风险 + 已知工具 + 安全缓存命中 -> 直接执行

进入条件：
- 产出了明确的候选 step
- `predict_risk = safe`
- `memory_for_tool = hit`

### 2. 无风险 + 已知工具 + 未命中缓存 + try 安全 -> try_commit

进入条件：
- `predict_risk = safe`
- `memory_for_tool = miss`
- `judge_try_result = safe`

### 3. 无风险 + 已知工具 + 未命中缓存 + 沙箱不安全 -> 拦截

进入条件：
- `predict_risk = safe`
- `memory_for_tool = miss`
- `tool_try` 后 `judge_try_result = unsafe`

## risky 主分支
### 4. 有风险 + 不需要人类 + replan 成功

进入条件：
- `predict_risk = risky`
- 风险根源是“当前方案不够好”，不是“信息缺失”
- agent 能自行想出更安全替代方案

### 5. 有风险 + 需要人类 -> ask_human

进入条件：
- `predict_risk = risky`
- 问题根源不是方案粗糙，而是缺少只有用户能提供的信息

### 6. 一开始直接 refuse

进入条件：
- 用户任务本身具有明确恶意意图
- 不存在通过补充信息就变成合法操作的可能

## ask_human 后续细分
### 7. ask_human -> 用户补充信息 -> 返回规划

进入条件：
- 用户愿意补充
- 用户回复提供了新的关键信息

### 8. ask_human -> 用户确认通过 -> 继续执行

进入条件：
- ask_human 的问题本质上是确认型而不是补充型
- 用户明确确认执行

### 9. ask_human -> 用户拒绝/不确认 -> 终止

进入条件：
- 用户明确拒绝
- 用户回答意味着不再继续当前任务

## replan 后续细分

### 10. replan -> 成功 -> 输出安全规划

进入条件：
- `replan` 生成了更好的新步骤
- 新步骤经过新一轮风险判断后进入 safe 或可控路径

### 11. replan -> 仍有风险 -> ask_human

进入条件：
- `replan` 之后仍然无法得到安全方案
- 说明问题不再只是“方案差”，而是缺用户信息

## tool_try 不安全后续细分

### 12. tool_try 不安全 -> ask_human（先回滚）

进入条件：
- `judge_try_result = unsafe`
- 且要继续安全完成任务必须用户补充信息

### 13. tool_try 不安全 -> 终止

进入条件：
- `judge_try_result = unsafe`
- 不存在安全替代方案，或者用户不希望继续





