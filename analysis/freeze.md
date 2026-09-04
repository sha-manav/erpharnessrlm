# Freeze checklist (P3.7)

The harness version that produced the eval100 result is main at `1f9d91f` ("eval100:
C_full 72/100 vs pi 39/100"), tagged `harness-v1-eval100`. Facts a reader needs:

- **Code the eval ran on.** Batches 1–5 ran from the working tree at commits between
  `f599ec7` (launch, 19 trials started) and `df8f61b` (the finished-goods origin rule,
  applied mid-run — trials load the library at kernel start, so trials started after
  it ran the corrected `origin_flow` and `create_po` guard). No other library change
  happened during the eval; later commits are run tooling, notes, analysis and the
  merge script.
- **Eval isolation.** `configs/eval100.txt` is frozen; `scripts/guard.sh` runs on every
  commit and refuses any read of an eval task's `tests/`, `solution/` or grader.
  Dev-task graders (dev40) were read during iteration, as the plan allows; nothing under
  `harness/` is specific to a task pattern.
- **Model and routing.** `z-ai/glm-5.1` via OpenRouter, fp8 only, providers
  GMICloud → Baidu → StreamLake (order preference, no fallback outside the list);
  `max_tokens` 16384; no temperature sent (provider default, same as pi). Baidu served
  nearly every eval request.
- **Caps.** step_cap 150, token_cap 3,000,000 input tokens, kernel timeout 600 s,
  time budget 3,300 s inside the task's 3,600 s agent timeout.
- **Batches.** Five run directories merged by `scripts/merge_batches.py` (first
  completed result per task; api_error/crash excluded): a concurrency restart, two
  network drops, one credit floor. Five tasks were rerun after dying to the second
  drop; 2297 scored 99.9 on its killed attempt and 20.4 on the rerun, and the rerun
  stands.
- **Reproduce.** `python3 scripts/run.py --config C_full --model big --set eval100 -n 12
  --background`, then `scripts/ingest_harbor.py <run>` and `scripts/merge_batches.py
  --set eval100 <runs…>`; `scripts/stats.py` for the paired comparison,
  `scripts/figures.py` for the plots.
