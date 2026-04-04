#!/usr/bin/env python3
import argparse
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import safety_pipeline.llm as llm
import safety_pipeline.memory as memory
import safety_pipeline.runtime as runtime
from safety_pipeline.memory import DisabledPlanMemoryStore, ExperienceMemory, PlanMemoryVectorStore, ToolMemory
from safety_pipeline.runtime import TOOL_AGENT_SYSTEM_PROMPT, load_task_file, pipeline
from safety_pipeline.settings import set_pipeline_env


REPORT_NOTE = (
    "本报告使用隔离 memory 目录重新执行同一任务两次。"
    "第一次是 cold start，第二次复用第一次执行后写入的 plan/tool memory。"
    "这样可以精确展示 `memory miss -> tool_try` 和 `memory hit -> direct_tool` 两条真实路径，"
    "同时不会污染仓库当前已有的 memory 文件。"
)


def json_ready(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def dump_json(value):
    return json.dumps(json_ready(value), ensure_ascii=False, indent=2)


def patch_runtime_memory(memory_root):
    memory_root.mkdir(parents=True, exist_ok=True)

    exp_store = ExperienceMemory(str(memory_root / "experience_memory.json"))
    tool_store = ToolMemory(str(memory_root / "tool_memory.json"))

    try:
        plan_store = PlanMemoryVectorStore(
            str(memory_root / "plan_memory.faiss"),
            str(memory_root / "plan_memory_meta.json"),
            exp_store,
        )
        memory._plan_memory_disabled_reason = None
    except RuntimeError as exc:
        plan_store = DisabledPlanMemoryStore(str(exc))
        memory._plan_memory_disabled_reason = str(exc)

    memory.experience_memory = exp_store
    memory.tool_memory = tool_store
    memory.plan_memory_store = plan_store

    runtime.experience_memory = exp_store
    runtime.tool_memory = tool_store

    return exp_store, tool_store, plan_store


def build_real_tool_explanation(real_tool_name):
    mapping = {
        "list_issues": [
            "`direct_tool/tool_try` 最终都会走到 `GiteaBackend.execute_tool()` 或 `GiteaBackend.run_try()`。",
            "真实工具实现是 `safety_pipeline/gitea_tools.py:387` 的 `list_issues()`。",
            "它内部通过 `safety_pipeline/gitea_tools.py:85` 的 `_api_json()` 调 Gitea HTTP API，底层请求函数是 `safety_pipeline/gitea_tools.py:74` 的 `_api()`。",
            "这一轮实际调用的 endpoint 是 `GET /api/v1/repos/{owner}/{repo}/issues`，并带上 `state` / `type=issues` / `limit` 参数。",
        ],
        "get_issue": [
            "`direct_tool/tool_try` 最终会调用 `safety_pipeline/gitea_tools.py:871` 的 `get_issue()`。",
            "它通过 `_api_json()` 请求 `GET /api/v1/repos/{owner}/{repo}/issues/{issue_iid}`。",
            "返回值在 `gitea_tools._format_issue()` 中被整理成 `iid/title/state/comments/body` 等字段，再作为字符串写回 pipeline state。",
        ],
        "list_issue_comments": [
            "`direct_tool/tool_try` 最终会调用 `safety_pipeline/gitea_tools.py:897` 的 `list_issue_comments()`。",
            "它通过 `_api_json()` 请求 `GET /api/v1/repos/{owner}/{repo}/issues/{issue_iid}/comments`。",
            "每条 comment 会被整理成 `id/author/created_at/updated_at/body`，然后整个列表作为 JSON 字符串返回。",
        ],
    }
    return mapping.get(real_tool_name, [])


def explain_event(event):
    output = event.get("model_output") or {}
    dispatch = event.get("dispatch") or {}
    tool_name = output.get("name", "")
    lines = [
        "这一轮进入模型前，主循环在 `safety_pipeline/runtime.py:1975` 中根据当前 `flow_phase` 调用工具选择函数。",
        "模型收到的 JSON snapshot 由 `safety_pipeline/runtime.py:453` 的 `build_agent_state_snapshot()` 生成。",
        "模型可见的工具列表由 `safety_pipeline/runtime.py:585` 的 `build_available_tool_schemas()` 生成；候选真实工具来自 `safety_pipeline/runtime.py:1618` 和 `safety_pipeline/tool_retrieval.py:194`。",
    ]

    if event.get("llm_mode") == "auto":
        lines.append(
            "这一轮是 `need_next_or_done`，因此 runtime 调的是 `safety_pipeline/llm.py:69` 的 `call_auto_tool_choice()`；模型可以选择继续调工具，也可以直接输出文本结束任务。"
        )
    else:
        lines.append(
            "这一轮使用 `safety_pipeline/llm.py:52` 的 `call_required_tool_choice()`，模型必须返回一个 tool call。"
        )

    if tool_name == "predict_risk":
        selected = ((output.get("arguments") or {}).get("tool")) or ""
        lines.extend(
            [
                "模型输出 `predict_risk` 后，runtime 进入 `safety_pipeline/runtime.py:1694` 的 `flow_tool_predict_risk()`。",
                "参数校验由 `safety_pipeline/runtime.py:285` 的 `validate_predict_risk_args()` 和 `safety_pipeline/runtime.py:228` 的 `validate_real_tool_step()` 完成；缺字段会触发 retry，并把错误写进 `last_tool_error`。",
            ]
        )
        if selected:
            lines.append(
                f"这轮实际挑中的真实工具是 `{selected}`；`predict_risk.result=safe` 时，runtime 会自动调用 `safety_pipeline/memory.py:590` 的 `memory_for_tool()` 查询该工具的安全样例。"
            )
            lines.extend(build_real_tool_explanation(selected))
        if dispatch.get("error"):
            lines.append(
                "这轮 dispatch 失败是因为 schema 校验没过；错误会在主循环 `safety_pipeline/runtime.py:2084` 写入 `last_tool_error`，下一轮 snapshot 会把它暴露给模型。"
            )
    elif tool_name == "tool_try":
        current_step = (((event.get("snapshot") or {}).get("current_step")) or {})
        real_tool = current_step.get("tool", "")
        lines.extend(
            [
                "模型输出 `tool_try` 后，runtime 进入 `safety_pipeline/runtime.py:1739` 的 `flow_tool_try()`。",
                "随后调用 `GiteaBackend.run_try()`，实现位置是 `safety_pipeline/environment.py:465`。",
            ]
        )
        if real_tool:
            lines.append(f"这轮 `tool_try` 试跑的真实工具是 `{real_tool}`。")
            lines.extend(build_real_tool_explanation(real_tool))
        lines.append(
            "这次 trace 里的真实工具都是只读工具，所以 `run_try()` 直接调用真实 Gitea API，不会创建 Docker checkpoint；如果是真写工具，才会进入 `safety_pipeline/environment.py:376` 的 `_create_try_checkpoint()`。"
        )
    elif tool_name == "judge_try_result":
        lines.extend(
            [
                "模型输出 `judge_try_result` 后，runtime 进入 `safety_pipeline/runtime.py:1751` 的 `flow_tool_judge_try_result()`。",
                "当判断结果是 `safe` 时，backend 会先 `commit_try()`，然后把这次真实工具执行写入 `tool_memory.store_safe_case()`，代码在 `safety_pipeline/memory.py:477`。",
                "随后 `update_state_from_execution()` 会把执行结果写入 `results/known_context/dialogue_history`，实现位置在 `safety_pipeline/state.py:162`。",
                "最后 `record_current_experience()` 会把这一步整条 trace 存进 experience memory，供下次 `memory_for_plan` 检索。",
            ]
        )
    elif tool_name == "direct_tool":
        current_step = (((event.get("snapshot") or {}).get("current_step")) or {})
        real_tool = current_step.get("tool", "")
        lines.extend(
            [
                "模型输出 `direct_tool` 后，runtime 进入 `safety_pipeline/runtime.py:1923` 的 `flow_tool_direct_tool()`。",
                "真实执行链路是 `execute_real_tool()` -> `GiteaBackend.execute_tool()` -> `gitea_tools.call_tool()`。",
            ]
        )
        if real_tool:
            lines.append(f"这一轮真正执行的 Gitea 工具是 `{real_tool}`。")
            lines.extend(build_real_tool_explanation(real_tool))
        lines.append(
            "能走到 `direct_tool` 说明前一步 `memory_for_tool.hit=true`，因此 runtime 直接跳过 `tool_try`。"
        )
    elif event.get("model_output", {}).get("type") == "text":
        lines.extend(
            [
                "这一轮模型没有再选工具，而是在 `need_next_or_done` 阶段直接输出文本。",
                "主循环会把文本记到 `final_reply`，然后把任务状态置为 `done`，实现位置在 `safety_pipeline/runtime.py:2018` 附近。",
            ]
        )

    return lines


class TraceRecorder:
    def __init__(self):
        self.events_by_run = []
        self.current_run_index = -1
        self.pending_event = None

        self._orig_required = runtime.call_required_tool_choice
        self._orig_auto = llm.call_auto_tool_choice
        self._orig_dispatch = runtime.dispatch_tool_call
        self._orig_persist = runtime.persist_local_artifacts

    def start_run(self):
        self.current_run_index += 1
        self.events_by_run.append([])
        self.pending_event = None

    def _new_event(self, llm_mode, system_prompt, snapshot, tools, model_output):
        event = {
            "llm_call_index": len(self.events_by_run[self.current_run_index]) + 1,
            "llm_mode": llm_mode,
            "system_prompt": system_prompt,
            "snapshot": json_ready(snapshot),
            "tools": json_ready(tools),
            "model_output": json_ready(model_output),
        }
        self.events_by_run[self.current_run_index].append(event)
        return event

    def install(self):
        def required_wrapper(system_prompt, snapshot, tools):
            tool_call = self._orig_required(system_prompt, snapshot, tools)
            raw_args = tool_call.function.arguments or "{}"
            try:
                parsed_args = json.loads(raw_args)
            except json.JSONDecodeError:
                parsed_args = {"_raw": raw_args}
            event = self._new_event(
                "required",
                system_prompt,
                snapshot,
                tools,
                {
                    "type": "tool_call",
                    "id": getattr(tool_call, "id", ""),
                    "name": tool_call.function.name,
                    "arguments": parsed_args,
                    "arguments_raw": raw_args,
                },
            )
            self.pending_event = event
            return tool_call

        def auto_wrapper(system_prompt, snapshot, tools):
            tool_call, text_reply = self._orig_auto(system_prompt, snapshot, tools)
            if tool_call is not None:
                raw_args = tool_call.function.arguments or "{}"
                try:
                    parsed_args = json.loads(raw_args)
                except json.JSONDecodeError:
                    parsed_args = {"_raw": raw_args}
                event = self._new_event(
                    "auto",
                    system_prompt,
                    snapshot,
                    tools,
                    {
                        "type": "tool_call",
                        "id": getattr(tool_call, "id", ""),
                        "name": tool_call.function.name,
                        "arguments": parsed_args,
                        "arguments_raw": raw_args,
                    },
                )
                self.pending_event = event
            else:
                self._new_event(
                    "auto",
                    system_prompt,
                    snapshot,
                    tools,
                    {
                        "type": "text",
                        "content": text_reply,
                    },
                )
                self.pending_event = None
            return tool_call, text_reply

        def dispatch_wrapper(state, tool_name, args):
            try:
                result = self._orig_dispatch(state, tool_name, args)
                if self.pending_event is not None:
                    self.pending_event["dispatch"] = {
                        "tool_name": tool_name,
                        "arguments": json_ready(args),
                        "result": json_ready(result),
                    }
                return result
            except Exception as exc:
                if self.pending_event is not None:
                    self.pending_event["dispatch"] = {
                        "tool_name": tool_name,
                        "arguments": json_ready(args),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                raise
            finally:
                self.pending_event = None

        def persist_wrapper():
            store = memory.get_plan_memory_store()
            store.sync_with_experience()
            return {"status": "isolated_memory_synced"}

        runtime.call_required_tool_choice = required_wrapper
        llm.call_auto_tool_choice = auto_wrapper
        runtime.dispatch_tool_call = dispatch_wrapper
        runtime.persist_local_artifacts = persist_wrapper

    def uninstall(self):
        runtime.call_required_tool_choice = self._orig_required
        llm.call_auto_tool_choice = self._orig_auto
        runtime.dispatch_tool_call = self._orig_dispatch
        runtime.persist_local_artifacts = self._orig_persist


def build_run_summary(run_payload):
    tool_memory_hits = []
    for trace in run_payload["pipeline_result"].get("decision_trace", []):
        tool_memory = trace.get("tool_memory") or {}
        hit = tool_memory.get("hit")
        step = trace.get("step") or {}
        if step.get("tool"):
            tool_memory_hits.append(
                {
                    "tool": step.get("tool"),
                    "hit": hit,
                    "summary": tool_memory.get("summary", ""),
                    "execution_method": ((trace.get("execution") or {}).get("method", "")),
                }
            )
    return {
        "status": run_payload["pipeline_result"].get("status"),
        "tool_memory_hits": tool_memory_hits,
        "result_tools": [
            {
                "tool": item.get("tool"),
                "method": item.get("method"),
            }
            for item in run_payload["pipeline_result"].get("results", [])
        ],
    }


def build_markdown(report):
    lines = []
    lines.append("# Pipeline 逐步 Trace 文档")
    lines.append("")
    lines.append(f"- 生成时间: `{report['generated_at']}`")
    lines.append(f"- 任务文件: `{report['task_file']}`")
    lines.append(f"- 任务环境: `{report['environment']}`")
    lines.append(f"- 任务文本: `{report['task_text']}`")
    lines.append(f"- 说明: {REPORT_NOTE}")
    lines.append("")
    lines.append("## 固定系统提示词")
    lines.append("")
    lines.append("```text")
    lines.append(report["system_prompt"])
    lines.append("```")
    lines.append("")
    lines.append("## 隔离 Memory 最终状态")
    lines.append("")
    lines.append("```json")
    lines.append(dump_json(report["isolated_memory"]))
    lines.append("```")
    lines.append("")

    for run in report["runs"]:
        run_no = run["run_index"]
        lines.append(f"## Run {run_no}")
        lines.append("")
        lines.append("### Run 摘要")
        lines.append("")
        lines.append("```json")
        lines.append(dump_json(run["summary"]))
        lines.append("```")
        lines.append("")

        for event in run["events"]:
            lines.append(f"### Run {run_no} / LLM Call {event['llm_call_index']}")
            lines.append("")
            lines.append(f"- `flow_phase`: `{((event.get('snapshot') or {}).get('flow_phase', ''))}`")
            lines.append(f"- `llm_mode`: `{event.get('llm_mode', '')}`")
            lines.append("")
            lines.append("#### 模型真实可见 Context")
            lines.append("")
            lines.append("`snapshot`:")
            lines.append("")
            lines.append("```json")
            lines.append(dump_json(event.get("snapshot")))
            lines.append("```")
            lines.append("")
            lines.append("`tools`:")
            lines.append("")
            lines.append("```json")
            lines.append(dump_json(event.get("tools")))
            lines.append("```")
            lines.append("")
            lines.append("#### 模型输出")
            lines.append("")
            lines.append("```json")
            lines.append(dump_json(event.get("model_output")))
            lines.append("```")
            lines.append("")
            lines.append("#### 工具调用与结果")
            lines.append("")
            lines.append("```json")
            lines.append(dump_json(event.get("dispatch")))
            lines.append("```")
            lines.append("")
            lines.append("#### 代码讲解")
            lines.append("")
            for item in explain_event(event):
                lines.append(f"- {item}")
            lines.append("")

        lines.append(f"### Run {run_no} / 完整 pipeline_result")
        lines.append("")
        lines.append("```json")
        lines.append(dump_json(run["pipeline_result"]))
        lines.append("```")
        lines.append("")

    lines.append("## Docker 相关代码说明")
    lines.append("")
    lines.append("- 这次选择的是只读 Gitea 任务，所以 `tool_try` 走的是 `safety_pipeline/environment.py:465` 的只读分支，没有创建 Docker checkpoint。")
    lines.append("- 如果换成写工具，`GiteaBackend.run_try()` 会先进入 `safety_pipeline/environment.py:376` 的 `_create_try_checkpoint()`：它会 `docker inspect` 找到 Gitea `/data` 挂载点，必要时 `docker stop/start` 容器，并复制 volume/bind mount 做快照。")
    lines.append("- 写工具被模型判成 `safe` 且 `judge_try_result=safe` 时，runtime 会调用 `commit_try()`；如果 `judge_try_result=unsafe` 且后续走 `ask_human`，则会触发 `rollback_try()` 把 Docker 数据卷恢复到试跑前状态。")
    lines.append("- 本地 Gitea 服务的启动和种子数据准备脚本是 `scripts/setup_gitea_env.sh`。本次实际跑 trace 前就是用这个脚本拉起的 Docker 服务。")
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Run a pipeline task twice and build a full context trace report.")
    parser.add_argument("--task-file", required=True, help="Task yaml path.")
    parser.add_argument("--output-dir", required=True, help="Directory used to write markdown/json report and isolated memory.")
    args = parser.parse_args()

    task_file = Path(args.task_file).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    task_config = load_task_file(str(task_file))
    environment = task_config.get("environment") or "gitea"
    set_pipeline_env(environment)

    isolated_memory_dir = output_dir / "isolated_memory"
    exp_store, tool_store, plan_store = patch_runtime_memory(isolated_memory_dir)

    recorder = TraceRecorder()
    recorder.install()

    runs = []
    try:
        for run_index in (1, 2):
            recorder.start_run()
            result = pipeline(
                task_config["task"],
                npc_scenario=task_config.get("scenarios") or None,
                task_config=task_config,
            )
            run_payload = {
                "run_index": run_index,
                "events": copy.deepcopy(recorder.events_by_run[run_index - 1]),
                "pipeline_result": json_ready(result),
            }
            run_payload["summary"] = build_run_summary(run_payload)
            runs.append(run_payload)
    finally:
        recorder.uninstall()

    final_tool_memory = json.loads((isolated_memory_dir / "tool_memory.json").read_text(encoding="utf-8"))
    final_experience_memory = json.loads((isolated_memory_dir / "experience_memory.json").read_text(encoding="utf-8"))
    plan_meta_path = isolated_memory_dir / "plan_memory_meta.json"
    final_plan_meta = []
    if plan_meta_path.exists():
        final_plan_meta = json.loads(plan_meta_path.read_text(encoding="utf-8"))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "task_file": str(task_file),
        "environment": environment,
        "task_text": task_config["task"],
        "system_prompt": TOOL_AGENT_SYSTEM_PROMPT,
        "runs": runs,
        "isolated_memory": {
            "tool_memory": final_tool_memory,
            "experience_memory_tail": final_experience_memory[-10:],
            "plan_memory_meta": final_plan_meta,
        },
    }

    report_json_path = output_dir / "trace.json"
    report_md_path = output_dir / "trace.md"
    report_json_path.write_text(dump_json(report) + "\n", encoding="utf-8")
    report_md_path.write_text(build_markdown(report) + "\n", encoding="utf-8")

    print(json.dumps(
        {
            "trace_json": str(report_json_path),
            "trace_md": str(report_md_path),
            "isolated_memory_dir": str(isolated_memory_dir),
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
