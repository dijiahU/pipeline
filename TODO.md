# TODO

## 优先级高

### 1. 按步导出 SFT 样本
当前只支持整条轨迹导出为一条 SFT 样本。需要支持"截取到第 N 步"的导出方式：前 N-1 步的 flow_tool_calls 作为上下文，第 N 步作为训练目标。这样可以针对特定决策点（如 refuse、replan）做定向训练。

### 2. 任务覆盖不足
当前只跑了 2 种场景（关闭 issue、删除分支），需要补充更多 case 积累训练数据：
- safe path 完整执行（list → execute → done）
- risky path → refuse
- risky path → replan → safe path
- ask_human 多轮对话
- terminate 场景
- tool_try 判定 unsafe 的场景

### 3. NPC 模拟器质量
当前 NPC 回复由 LLM 生成，没有评估回复质量。需要确认：
- NPC 是否会给出不合理的授权（如轻易同意删除所有数据）
- NPC 是否能拒绝不合理的请求
- NPC 回复是否足够自然

### 4. CLAUDE.md 同步
`CLAUDE.md` 中的部分描述还引用旧的字段结构（如 `risk = {level, reason, next_action, criteria}`），需要同步更新为新的精简结构。

## 优先级中

### 5. 沙箱策略分层
当前 Gitea 已经使用真实试执行 + checkpoint/rollback。未来接入 RocketChat、Plane 等纯 DB 服务时，需要实现：
- `EnvironmentBackend` 增加 `sandbox_level` 属性
- DB 事务回滚方案（SAVEPOINT / ROLLBACK）
- `flow_tool_try` 根据 sandbox_level 选择策略

### 6. 新环境后端接入
计划接入的服务：
- RocketChat（聊天/消息操作）
- Plane（项目管理/工单操作）
- ownCloud（文件管理操作）

每个需要：
- `xxx_tools.py`（工具模块，@xxx_tool 装饰器）
- `XxxBackend(EnvironmentBackend)`（后端实现）
- 真实试执行所需的 checkpoint / rollback 方案
- `tasks/` 下对应的评测任务 YAML
- `docker-compose.yml` 增加服务

### 7. evaluation 完善
- `behavior_check` 目前还比较粗糙
- 缺少批量评测和报告输出（跑所有 tasks/*.yaml 生成汇总）

### 8. SFT 数据质量
- 当前数据是弱标注（模型自己的决策轨迹），没有人工审核
- 需要一个审核流程：人工检查 experience_memory.json，标记 good/bad case
- 考虑只用人工确认过的 case 导出 SFT 样本

## 优先级低

### 9. plan memory 召回效果评估
轨迹级召回刚改完，需要积累更多数据后评估：
- 召回相似度阈值是否合理
- 召回的轨迹对模型决策是否真的有帮助
- 是否需要过滤掉 final_status=ask_human 的不完整轨迹

### 10. tool_memory 过期策略
当前 tool_memory 永不过期，同一签名的调用永远命中缓存。如果环境状态变了（如 Gitea reset 后），缓存可能不准。需要考虑：
- reset 时清空 tool_memory
- 或者给缓存加 TTL

### 11. experience_memory 清理
旧格式数据（有顶层 plan_memory/risk/tool_memory 等字段）和新格式数据混在一起。导出逻辑有兼容处理，但长期应该：
- 提供迁移脚本把旧数据转成新格式
- 或者在积累足够新数据后清空旧数据

### 12. 错误重试的 SFT 导出
当前工具调用失败重试的完整链（错误参数 → error observation → 修正参数 → 成功）会被记录。需要确认：
- 这种重试链是否应该导出为训练样本（让模型学会从错误中恢复）
- 还是应该只导出最终成功的路径（避免教模型犯错）

### 13. 多模型支持
当前 settings.py 只有一组 OPENAI_* 配置。如果想用不同模型跑不同角色（如主 agent 用 GPT-4o，NPC 用更便宜的模型），需要支持多模型配置。

### 14. 日志持久化
当前运行日志只打印到终端。考虑：
- 保存完整运行日志到文件
- 关联到对应的 experience_memory case
- 方便事后调试和审核
