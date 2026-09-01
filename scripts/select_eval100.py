#!/usr/bin/env python3
"""P1.1 — freeze the evaluation split and derive the development splits.

`configs/eval100.txt` is **immutable once written** (PLAN.md hard rule 5) and, from the
moment it exists, every `tests/` and `solution/` directory it names is off limits
(`scripts/guard.sh`). Everything not in it becomes `configs/dev.txt`, the only pool the
harness is allowed to be tuned against.

Sampling
--------
Stratified by `task_pattern`, the metadata field that names the generator template
(29 patterns over the 300 shipped tasks). Three per pattern gives 87; the remaining 13
go round-robin over patterns ordered by descending pool size, so the extras land where
there is the most to sample from. `random.Random(0)` makes it reproducible.

The Phase-0 dev task is excluded by name: it was opened, run and poked at during
discovery, so it can never be an eval task.

Refuses to overwrite an existing eval100.txt without --force (which exists only for
re-running before the freeze commit — never after).
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.discover import read_task_meta  # noqa: E402

# Opened during Phase 0 discovery (NOTES.md ## ERP-Bench / Dev task).
EXCLUDED_FROM_EVAL = {"2000_easy_01_buy_only_baseline"}

EVAL_N = 100
EVAL_PER_PATTERN = 3
EVAL_SEED = 0
DEV40_N = 40
DEV40_SEED = 2


def load_tasks(tasks_dir: Path) -> dict[str, str]:
    """task_id -> task_pattern, for every task that declares one."""
    tasks: dict[str, str] = {}
    for task_dir in sorted(tasks_dir.iterdir()):
        if not (task_dir / "task.toml").exists():
            continue
        pattern = read_task_meta(task_dir).get("task_pattern")
        if pattern:
            tasks[task_dir.name] = pattern
        else:
            print(f"warning: {task_dir.name} has no task_pattern; skipped", file=sys.stderr)
    return tasks


def stratified_sample(
    by_pattern: dict[str, list[str]], total: int, per_pattern: int, seed: int
) -> list[str]:
    """`per_pattern` from every pattern, then round-robin extras until `total`.

    Pools are shuffled with `seed` so the choice within a pattern is reproducible but not
    an artefact of directory order (which correlates with scenario number, hence with the
    generator's own sampling order).
    """
    rng = random.Random(seed)
    pools = {}
    for pattern, ids in by_pattern.items():
        shuffled = list(ids)
        rng.shuffle(shuffled)
        pools[pattern] = shuffled

    picked: list[str] = []
    for pattern in sorted(pools):
        picked.extend(pools[pattern][:per_pattern])

    if len(picked) > total:
        raise SystemExit(
            f"{per_pattern} per pattern over {len(pools)} patterns is {len(picked)} > {total}"
        )

    # Extras go to the biggest remaining pools first, so no pattern is drained.
    order = sorted(pools, key=lambda p: (-len(pools[p]), p))
    taken = {pattern: per_pattern for pattern in pools}
    while len(picked) < total:
        progressed = False
        for pattern in order:
            if len(picked) >= total:
                break
            index = taken[pattern]
            if index < len(pools[pattern]):
                picked.append(pools[pattern][index])
                taken[pattern] = index + 1
                progressed = True
        if not progressed:
            raise SystemExit(f"ran out of tasks at {len(picked)}/{total}")
    return picked


def write_list(path: Path, ids: list[str], header: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(ids)
    path.write_text(f"# {header}\n# {len(ids)} task ids — do not edit by hand.\n{body}\n")
    try:
        shown = path.relative_to(REPO_ROOT)
    except ValueError:
        shown = path
    print(f"wrote {shown}  ({len(ids)} ids)")


def summarise(name: str, ids: list[str], patterns: dict[str, str]) -> None:
    counts: dict[str, int] = defaultdict(int)
    for task_id in ids:
        counts[patterns[task_id]] += 1
    print(
        f"{name}: {len(ids)} tasks, {len(counts)} patterns, "
        f"min/max per pattern {min(counts.values())}/{max(counts.values())}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks-dir", default=str(REPO_ROOT / "vendor/erp-bench/tasks"))
    parser.add_argument("--configs-dir", default=str(REPO_ROOT / "configs"))
    parser.add_argument("--force", action="store_true", help="overwrite an existing eval100.txt")
    args = parser.parse_args()

    configs_dir = Path(args.configs_dir)
    eval_path = configs_dir / "eval100.txt"
    if eval_path.exists() and not args.force:
        raise SystemExit(
            f"{eval_path} already exists and is frozen (PLAN.md hard rule 5). "
            "Pass --force only if the freeze has not been committed yet."
        )

    patterns = load_tasks(Path(args.tasks_dir))
    if not patterns:
        raise SystemExit(f"no tasks with a task_pattern under {args.tasks_dir}")

    eligible: dict[str, list[str]] = defaultdict(list)
    for task_id, pattern in patterns.items():
        if task_id not in EXCLUDED_FROM_EVAL:
            eligible[pattern].append(task_id)

    eval_ids = stratified_sample(eligible, EVAL_N, EVAL_PER_PATTERN, EVAL_SEED)
    assert len(set(eval_ids)) == EVAL_N, "eval100 must hold 100 unique ids"
    assert not (set(eval_ids) & EXCLUDED_FROM_EVAL)

    dev_ids = sorted(set(patterns) - set(eval_ids))
    dev_by_pattern: dict[str, list[str]] = defaultdict(list)
    for task_id in dev_ids:
        dev_by_pattern[patterns[task_id]].append(task_id)

    dev40 = stratified_sample(dev_by_pattern, DEV40_N, 1, DEV40_SEED)

    write_list(eval_path, sorted(eval_ids), "FROZEN eval set — never read these tasks' tests/ or solution/")
    write_list(configs_dir / "dev.txt", dev_ids, "development pool (everything not in eval100)")
    write_list(configs_dir / "dev40.txt", dev40, "dev curve set (stratified, seed=2)")
    write_list(configs_dir / "dev10.txt", dev40[:10], "quick iteration set (first 10 of dev40)")
    write_list(configs_dir / "dev5.txt", dev40[:5], "smoke set (first 5 of dev40)")

    print()
    summarise("eval100", sorted(eval_ids), patterns)
    summarise("dev", dev_ids, patterns)
    summarise("dev40", dev40, patterns)
    print(f"\nexcluded from eval by name: {sorted(EXCLUDED_FROM_EVAL)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
