# TODO

## Goal

Produce stable multi-turn tool-calling trajectories from `pipeline.py` that can be used by LLaMAFactory for SFT with minimal manual cleanup.

## Blocking Issues

1. Replan export is still incomplete.
   Current `experience_memory` stores the failed step, `risk_assessment`, `try_result`, and `try_judgment`, but the exported SFT session can still start from `replan` instead of including the full trigger chain. This must be fixed before training, otherwise the model learns broken mid-trajectory fragments.

2. `tool_try` observations are too heavy.
   `before_state` and `after_state` are still exported verbatim. For training, these should be compressed into a compact summary such as `exec_status`, `state_changed`, `changed_files_count`, `unexpected_side_effect`, and a short `summary`.

3. Memory is already polluted by debugging runs.
   `tool_memory.json` and parts of `experience_memory.json` contain synthetic or low-value cases from schema debugging. These should be cleared or filtered before generating a training set.

4. LLaMAFactory wiring is not finished.
   We still do not have `dataset_info.json`, loader validation, or a confirmed end-to-end run that proves the exported dataset is accepted by LLaMAFactory without extra conversion.

## Important But Not Blocking

1. Plan-memory retrieval quality is weak.
   Many retrieved neighbors are empty `ask_human` cases with no concrete step. Retrieval should rank step-bearing cases higher or filter empty-step samples.

2. Read-only commands are executed twice.
   Safe read-only commands still go through `tool_try` and then real execution. This is acceptable for now, but wasteful.

3. Error-feedback quality can improve further.
   `last_tool_error` and retry loops are working, but some messages still mention internal names like `current_step`, which is less clean than the public `tool/tool_args/description` schema.

## Definition Of Done For First Usable SFT Dataset

- Clean `memory/` and rerun a curated set of representative tasks.
- Export sessions with complete pre-replan chains.
- Replace raw `tool_try` snapshots with compact summaries.
- Verify the dataset loads in LLaMAFactory.
- Check a small sample set manually for schema correctness and trajectory completeness.

## Current Distance

Not far from an MVP, but not ready yet.

The runtime control flow is now close to target shape. The remaining gap is mostly in data quality and export fidelity, not the high-level agent loop. Once the exporter is fixed, `tool_try` is compressed, and memory is regenerated from clean runs, the project should be close to a first usable LLaMAFactory SFT dataset.
