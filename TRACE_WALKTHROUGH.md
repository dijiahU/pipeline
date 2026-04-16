# 轨迹说明

这份文档说明一条任务是如何从第一轮原始执行，变成第二轮拼接后的 `session_cases`，再变成最终 SFT 数据的。

整体流程分 4 段：

1. 第一轮 `pass1` 原始执行轨迹
2. 第二轮 `pass2` 风险判断结果
3. `splice()` 拼出来的 pipeline 风格 `session_cases`
4. 最终导出的 SFT 轨迹

下面统一用这个任务举例：

- 任务文件：`tasks/gitea/gitea-close-reopen-issue.yaml`
- 服务环境：`gitea`

## 当前 Flow Tool

现在保留的 flow tool 只有 4 个，它们只是合成轨迹里的保留标签，不是 `pass1` 可调用的真实工具：

- `direct_tool`
- `ask_human`
- `refuse`
- `replan`

含义分别是：

- `direct_tool`
  当前真实工具调用被直接放行并执行
- `ask_human`
  当前步骤需要先问人
- `refuse`
  当前目标不应该继续帮助
- `replan`
  当前步骤应该换成一个更安全的新步骤

## 第一轮 Pass1 原始轨迹

第一轮就是纯任务执行，不加任何安全流控。

这一轮模型看到的是一个很短的 snapshot，例如：

```json
{
  "user_task": "...",
  "service_context": {
    "service_id": "gitea",
    "environment": "gitea"
  },
  "results": []
}
```

第一轮落盘后的结果是最小 transcript：

```json
{
  "task": "...close then reopen issue #1...",
  "environment": "gitea",
  "transcript": [
    {
      "role": "user",
      "content": "...close then reopen issue #1..."
    },
    {
      "role": "assistant",
      "tool_call": {
        "tool": "close_issue",
        "tool_args": {
          "project_id": "openclaw",
          "issue_iid": 1
        },
        "description": "Execute close_issue."
      }
    },
    {
      "role": "tool",
      "tool": "close_issue",
      "observation": "Issue #1 ('Bug: macOS build instructions are outdated') was closed."
    },
    {
      "role": "assistant",
      "tool_call": {
        "tool": "reopen_issue",
        "tool_args": {
          "project_id": "openclaw",
          "issue_iid": 1
        },
        "description": "Execute reopen_issue."
      }
    },
    {
      "role": "tool",
      "tool": "reopen_issue",
      "observation": "Issue #1 ('Bug: macOS build instructions are outdated') was reopened."
    }
  ],
  "final_status": "done",
  "final_response": "Done — issue #1 in `openclaw` was closed and then reopened, and it is left open as requested."
}
```

这里最重要的点是：

- `pass1` 只保留 `user -> tool_call -> observation` 这种最小循环
- 不再保存大块 state
- 不再把旧的 flow decision 混进第一轮原始轨迹

## 第二轮 Pass2 风险判断

第二轮不会直接拿第一轮 transcript 原样输入模型，而是先把 transcript 还原成 step 列表：

```json
[
  {
    "step_index": 0,
    "tool": "close_issue",
    "tool_args": {
      "project_id": "openclaw",
      "issue_iid": 1
    },
    "description": "Execute close_issue.",
    "observation": "Issue #1 ('Bug: macOS build instructions are outdated') was closed."
  },
  {
    "step_index": 1,
    "tool": "reopen_issue",
    "tool_args": {
      "project_id": "openclaw",
      "issue_iid": 1
    },
    "description": "Execute reopen_issue.",
    "observation": "Issue #1 ('Bug: macOS build instructions are outdated') was reopened."
  }
]
```

然后 reviewer 对每个 step 单独判断。

第二轮模型实际看到的是一个压缩后的 payload：

- `task`
- `environment`
- `history`
  这里只保留最近的真实工具历史摘要，不给完整长历史
- `proposed_step`

这个例子里，第二轮返回的是：

```json
[
  {
    "tool": "close_issue",
    "tool_args": {
      "project_id": "openclaw",
      "issue_iid": 1
    },
    "description": "Execute close_issue.",
    "result": "direct_execute",
    "reasoning": "Explicit approval for workflow testing sprint and requested issue-state validation in Gitea; closing issue #1 is a bounded repository action matching the task."
  },
  {
    "tool": "reopen_issue",
    "tool_args": {
      "project_id": "openclaw",
      "issue_iid": 1
    },
    "description": "Execute reopen_issue.",
    "result": "direct_execute",
    "reasoning": "Workflow-testing task explicitly approves closing then reopening issue #1; prior step already closed it, and reopening leaves final state as requested."
  }
]
```

## Splice 阶段

`splice()` 做的事情是把：

- 第一轮真实执行轨迹
- 第二轮逐步判断结果

拼成一条更像 runtime 真实运行过的 `session_cases`。

它对每一步会做这几件事：

1. 通过 replay 前面真实执行过的工具结果，恢复“当前 step 之前”的状态
2. 基于这个状态构造当前决策点的紧凑 `context_snapshot`
3. 把第二轮 decision 写成统一的 case 结构
4. 把第一轮真实 observation 塞回这个 case

### 当前的 Context Snapshot

现在每个 case 里保留的 snapshot 很小，只包含：

- `prior_steps`
  最近真实工具执行的简短摘要
- `human_feedback`
  只有走过 `ask_human` 并且用户同意后续执行时才会出现

在这个例子里，第 2 步的 snapshot 是：

```json
{
  "prior_steps": [
    {
      "tool": "close_issue",
      "tool_args": {
        "project_id": "openclaw",
        "issue_iid": 1
      },
      "observation": "Issue #1 ('Bug: macOS build instructions are outdated') was closed."
    }
  ]
}
```

### `direct_execute` 被拼成什么

当第二轮说当前 step 是 `direct_execute` 时，splice 会生成一个 case：

- 顶层 `risk`
- `flow_tool_calls` 里一个 `direct_tool`

第 1 步对应的 case 形状是：

```json
{
  "task": "...",
  "step_index": 0,
  "context_snapshot": {},
  "risk": {
    "result": "direct_execute",
    "reasoning": "Explicit approval for workflow testing sprint and requested issue-state validation in Gitea; closing issue #1 is a bounded repository action matching the task."
  },
  "flow_tool_calls": [
    {
      "tool_name": "direct_tool",
      "result": {
        "tool": "close_issue",
        "tool_args": {
          "project_id": "openclaw",
          "issue_iid": 1
        },
        "exec_result": "Issue #1 ('Bug: macOS build instructions are outdated') was closed."
      }
    }
  ],
  "step": {
    "tool": "close_issue",
    "args": {
      "project_id": "openclaw",
      "issue_iid": 1
    },
    "description": "Execute close_issue."
  },
  "decision": "direct_tool",
  "outcome": "executed"
}
```

第 2 步也是同样结构，只是它的 `context_snapshot.prior_steps` 不为空。

## 拼完后的整体结果

两步都拼完以后，pipeline 风格的结果大致长这样：

```json
{
  "status": "done",
  "results": [
    {
      "tool": "close_issue",
      "args": {
        "project_id": "openclaw",
        "issue_iid": 1
      },
      "result": "Issue #1 ('Bug: macOS build instructions are outdated') was closed.",
      "method": "direct_tool"
    },
    {
      "tool": "reopen_issue",
      "args": {
        "project_id": "openclaw",
        "issue_iid": 1
      },
      "result": "Issue #1 ('Bug: macOS build instructions are outdated') was reopened.",
      "method": "direct_tool"
    }
  ],
  "decision_trace": [
    {
      "step_index": 0,
      "risk": {
        "level": "direct_execute",
        "reason": "..."
      },
      "execution": {
        "method": "direct_tool",
        "result": "Issue #1 ('Bug: macOS build instructions are outdated') was closed."
      }
    },
    {
      "step_index": 1,
      "risk": {
        "level": "direct_execute",
        "reason": "..."
      },
      "execution": {
        "method": "direct_tool",
        "result": "Issue #1 ('Bug: macOS build instructions are outdated') was reopened."
      }
    }
  ],
  "final_response": "Done — issue #1 in `openclaw` was closed and then reopened, and it is left open as requested."
}
```

这一步就是轨迹从“纯执行日志”变成“带安全决策语义的训练轨迹”的地方。

## 最终 SFT 导出

最终导出时，系统同时保留两件事：

1. 外层轨迹仍然是 prefix 风格
2. 每个当前 decision point 的 user snapshot 仍然是 fresh 风格、紧凑结构

所以最终导出的样本不是单轮孤立样本，而是：

- 第 1 条样本：只到第 1 个决策点
- 第 2 条样本：带上前面已有 decision turn 的前缀
- 后面依次类推

但是每一条样本里的“当前 user 消息”不是一个巨大的状态块，而是这种结构：

```json
{
  "user_task": "...",
  "service_context": {
    "service_id": "gitea",
    "environment": "gitea"
  },
  "assistant_proposed_tool_call": {
    "tool": "reopen_issue",
    "tool_args": {
      "project_id": "openclaw",
      "issue_iid": 1
    },
    "description": "Execute reopen_issue."
  },
  "prior_steps": [
    {
      "tool": "close_issue",
      "tool_args": {
        "project_id": "openclaw",
        "issue_iid": 1
      },
      "observation": "Issue #1 ('Bug: macOS build instructions are outdated') was closed."
    }
  ]
}
```

对这个例子来说，最后一条 SFT 样本大致是：

- `prompt` 里已经带上前面 decision turn 的前缀
- 最后一个 user turn 是当前 `reopen_issue` 的紧凑 snapshot
- `completion` 只有当前这一步的决策输出

例如：

```json
"<|direct_execute|>{\"reasoning\":\"Workflow-testing task explicitly approves closing then reopening issue #1; prior step already closed it, and reopening leaves final state as requested.\"}"
```

## 不同分支是怎么拼的

不同的第二轮 decision，会对应不同的 splice 分支：

- `direct_execute`
  - 写成 `risk + direct_tool`
- `ask_human`
  - 先写一个 `ask_human` case
  - 如果 synthetic reply 是 `approve`，再接一个 `direct_tool` case
  - 如果是 `reject`，轨迹就在这里停
- `refuse`
  - 写成 `risk + refuse`
  - 然后轨迹结束
- `replan`
  - 写成 `risk + replan`
  - 再从新的 step 重新跑 `pass1`
  - 然后递归继续 splice 新分支

## 当前设计的核心取舍

现在这条链路是刻意拆成两种上下文形态的：

- `pass1` 原始轨迹：尽量小，只保留执行日志
- `pass2` 模型输入：也是压缩 snapshot，控制 token 消耗
- `session_cases`：恢复成带安全决策语义的训练轨迹
- 最终 SFT 导出：保留 prefix 形式，训练数据更工整

一句话总结就是：

- 真正跑模型造数据时，用短 snapshot 省 token
- 最终导出训练集时，用长一些的前缀轨迹保留结构信息
