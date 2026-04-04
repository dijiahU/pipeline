# Translation TODO

## Goal

Translate all user-facing Chinese text in the repository into English without
changing stable identifiers, schema keys, or behavior.

## Rules

- Translate user-facing text only.
- Do not rename task `id`, `service`, `environment`, tool function names,
  outcome check `condition` values, API field names, or file names unless we
  explicitly decide to do a broader repo cleanup later.
- Keep YAML structure and Python logic unchanged.
- Preserve exact enum values and parameter keys that runtime code depends on.

## Priority 1: Task YAML Files

These files directly affect model prompts and scenario behavior, so they should
be translated first.

- [tasks/mailu](/Users/rick/Desktop/pipline/pipeline/tasks/mailu): 69/69 YAML files contain Chinese
- [tasks/owncloud](/Users/rick/Desktop/pipline/pipeline/tasks/owncloud): 42/42 YAML files contain Chinese
- [tasks/nocodb](/Users/rick/Desktop/pipline/pipeline/tasks/nocodb): 32/32 YAML files contain Chinese
- [tasks/zammad](/Users/rick/Desktop/pipline/pipeline/tasks/zammad): 58/58 YAML files contain Chinese
- [tasks/discourse](/Users/rick/Desktop/pipline/pipeline/tasks/discourse): 64/64 YAML files contain Chinese
- [tasks/rocketchat](/Users/rick/Desktop/pipline/pipeline/tasks/rocketchat): 80/80 YAML files contain Chinese
- [tasks/erpnext](/Users/rick/Desktop/pipline/pipeline/tasks/erpnext): 81/81 YAML files contain Chinese
- [tasks/openemr](/Users/rick/Desktop/pipline/pipeline/tasks/openemr): 69/69 YAML files contain Chinese
- [tasks/gitea](/Users/rick/Desktop/pipline/pipeline/tasks/gitea): 37/39 YAML files contain Chinese

Translate these fields inside task YAMLs:

- `task`
- `oracle.risk_source`
- `scenarios[].content`
- `scenarios.name`
- `scenarios.role`
- `scenarios.public_info`
- `scenarios.goal`
- `scenarios.strategy_hint`

Usually do not change these YAML fields:

- `id`
- `service`
- `environment`
- `oracle.preferred_action`
- `oracle.expected_flow`
- `oracle.outcome_check.*`

## Priority 2: Tool Definitions Exposed To The Model

These strings appear in tool descriptions, parameter descriptions, and tool
return messages. They should be translated after the task YAMLs because they
also affect model behavior.

Core tool files to translate:

- [safety_pipeline/gitea_tools.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/gitea_tools.py)
- [safety_pipeline/mailu_tools.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/mailu_tools.py)
- [safety_pipeline/nocodb_tools.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/nocodb_tools.py)
- [safety_pipeline/owncloud_tools.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/owncloud_tools.py)
- [safety_pipeline/zammad_tools.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/zammad_tools.py)
- [safety_pipeline/discourse_tools.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/discourse_tools.py)
- [safety_pipeline/rocketchat_tools.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/rocketchat_tools.py)
- [safety_pipeline/erpnext_tools.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/erpnext_tools.py)
- [safety_pipeline/openemr_tools.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/openemr_tools.py)
- [safety_pipeline/service_tools.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/service_tools.py)

Translate in these files:

- Tool description strings
- Parameter description strings
- `short_description`
- User-facing success messages
- User-facing error messages
- Inline Chinese comments that explain tool behavior

Do not change:

- Python function names
- Registered tool names
- Parameter keys
- Return JSON keys

## Priority 3: Runtime / Backend / Evaluation User-Facing Text

These files also contain Chinese, mostly in logs, error messages, and runtime
status text. Translate them after task YAMLs and tool definitions.

- [safety_pipeline/runtime.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/runtime.py)
- [safety_pipeline/environment.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/environment.py)
- [safety_pipeline/evaluation.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/evaluation.py)
- [safety_pipeline/backend_abc.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/backend_abc.py)
- [safety_pipeline/discourse_backend.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/discourse_backend.py)
- [safety_pipeline/erpnext_backend.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/erpnext_backend.py)
- [safety_pipeline/mailu_backend.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/mailu_backend.py)
- [safety_pipeline/openemr_backend.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/openemr_backend.py)
- [safety_pipeline/zammad_backend.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/zammad_backend.py)
- [safety_pipeline/console.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/console.py)
- [safety_pipeline/llm.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/llm.py)
- [safety_pipeline/memory.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/memory.py)
- [safety_pipeline/settings.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/settings.py)
- [safety_pipeline/state.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/state.py)
- [safety_pipeline/task_catalog.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/task_catalog.py)
- [safety_pipeline/tool_retrieval.py](/Users/rick/Desktop/pipline/pipeline/safety_pipeline/tool_retrieval.py)

Translate in these files:

- Runtime status text
- Exception messages
- Evaluation detail strings
- Console output
- Comments only if they are meant for maintainers and we want a fully English repo

## Priority 4: Repository Docs And Notes

If the goal is a fully English repository, these docs also need translation.
This is lower priority for runtime behavior.

- [README.md](/Users/rick/Desktop/pipline/pipeline/README.md)
- [TODO.md](/Users/rick/Desktop/pipline/pipeline/TODO.md)
- [任务构造方案.md](/Users/rick/Desktop/pipline/pipeline/任务构造方案.md)
- [服务扩展实施指南.md](/Users/rick/Desktop/pipline/pipeline/服务扩展实施指南.md)
- [SFT数据质量与采样方案.md](/Users/rick/Desktop/pipline/pipeline/SFT数据质量与采样方案.md)
- [训练流程问题分析.md](/Users/rick/Desktop/pipline/pipeline/训练流程问题分析.md)
- [训练流程图设计.md](/Users/rick/Desktop/pipline/pipeline/训练流程图设计.md)
- [criterion.md](/Users/rick/Desktop/pipline/pipeline/criterion.md)
- [branches.md](/Users/rick/Desktop/pipline/pipeline/branches.md)
- [changes.md](/Users/rick/Desktop/pipline/pipeline/changes.md)
- [service_catalog.md](/Users/rick/Desktop/pipline/pipeline/service_catalog.md)

## Suggested Execution Order

1. Translate all task YAMLs under `tasks/`.
2. Translate all `safety_pipeline/*_tools.py` files and `service_tools.py`.
3. Translate runtime/backend/evaluation user-facing strings.
4. Translate repo documentation.
5. Run YAML parsing and a fast Python syntax check after each batch.

## Validation Checklist

- All edited YAML files still parse with `yaml.safe_load`
- `python -m py_compile safety_pipeline/*.py` passes
- No stable identifier was renamed by mistake
- Tool schema keys and enum values remain unchanged
- Task `oracle.expected_flow` and `outcome_check` remain unchanged unless there
  is a deliberate fix
