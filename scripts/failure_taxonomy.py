#!/usr/bin/env python3
"""Prepare failed trajectories for hand-coding against the failure taxonomy.

    python3 scripts/failure_taxonomy.py runs/A_pi__big__dev40__... --out analysis/failures_stock_big.md

Used by P1.4 (stock harness, dev tasks) and P5.3 (our harness, eval tasks). It samples up
to `--limit` failed trials, and for each one prints what is needed to assign **one primary
code**: the reward breakdown, which rules the verifier marked FAIL, the terminal reason,
and a condensed trajectory (tool calls with truncated arguments and outputs).

What it reads: our own trajectories and the verifier's *output* (`reward.json`). It never
reads a task's `tests/` or `solution/` — the failed-rule names come from the reward file
the verifier writes, which is what makes coding eval failures possible without breaking
eval isolation (PLAN.md hard rule 1).

The output is a skeleton: every trial gets a `**Code:** ` line left blank, to be filled in
by reading. Counting is done afterwards by `--tally` over the completed file.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.discover import read_task_meta  # noqa: E402

TAXONOMY = {
    "SUPPLIER": "wrong vendor, below MOQ, wrong price",
    "TIMELINE": "receipt/production after the need date",
    "MISSING_DOC": "no delivery, or no invoice, or unposted invoice",
    "DRAFT": "dangling draft documents",
    "OVERSPEND": "policy/cap violated",
    "SUBOPTIMAL": "valid end state but not the cheapest plan",
    "BOM_MATH": "wrong quantities",
    "API_ERR": "could not execute an Odoo operation it needed",
    "TOOL_LOOP": "repeated failing calls",
    "STEP_CAP": "ran out of steps",
    "PREMATURE_FINISH": "finished with work undone",
    "SCOPE": "modified unrelated records",
    "OTHER": "",
}


def failed_rules(verifier_raw: dict, limit: int = 25) -> list[str]:
    by_dimension = ((verifier_raw.get("rules") or {}).get("by_dimension")) or {}
    out: list[str] = []
    for dimension, rules in by_dimension.items():
        for rule in rules if isinstance(rules, list) else []:
            if rule.get("status") == "FAIL" or rule.get("passed") is False:
                out.append(f"{dimension}: {rule.get('expr') or rule.get('rule')}")
    return out[:limit]


def condense(trajectory: Path, head: int, tail: int, width: int) -> list[str]:
    events = []
    for line in trajectory.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            continue

    def render(event: dict) -> str:
        step = event.get("t")
        who = event.get("agent_id", "root")
        role = event.get("role")
        tool = event.get("tool")
        if role == "tool":
            body = (event.get("output") or "").strip().replace("\n", " ⏎ ")
            return f"  [{step}] {who} <{tool} result> {body[:width]}"
        args = event.get("args")
        if isinstance(args, dict):
            args = json.dumps(args)
        body = (args or event.get("content") or "").strip().replace("\n", " ⏎ ")
        label = f"{tool}(" if tool else "say "
        return f"  [{step}] {who} {label}{body[:width]}"

    if len(events) <= head + tail:
        return [render(e) for e in events]
    return (
        [render(e) for e in events[:head]]
        + [f"  … {len(events) - head - tail} events elided …"]
        + [render(e) for e in events[-tail:]]
    )


def write_report(
    run_dir: Path, out_path: Path, limit: int, seed: int, head: int, tail: int, width: int,
    tasks_dir: Path,
) -> int:
    results = []
    for result_path in sorted(run_dir.glob("*/result.json")):
        result = json.loads(result_path.read_text())
        if result.get("pass") is not True:
            results.append((result_path.parent, result))

    if not results:
        print(f"{run_dir.name}: no failed trials")
        return 0

    rng = random.Random(seed)
    sample = results if len(results) <= limit else rng.sample(results, limit)
    sample.sort(key=lambda pair: pair[1]["task_id"])

    meta = json.loads((run_dir / "meta.json").read_text()) if (run_dir / "meta.json").exists() else {}

    lines = [
        f"# Failure analysis — {run_dir.name}",
        "",
        f"config `{meta.get('config', {}).get('id')}` · model `{meta.get('model', {}).get('model')}` · "
        f"commit `{(meta.get('commit') or '')[:8]}`",
        "",
        f"{len(results)} of {len(list(run_dir.glob('*/result.json')))} trials failed; "
        f"{len(sample)} sampled (seed {seed}).",
        "",
        "## Taxonomy",
        "",
        *[f"- **{code}** — {desc}" for code, desc in TAXONOMY.items()],
        "",
        "Assign exactly one primary code per trial on the `**Code:**` line, then run "
        "`scripts/failure_taxonomy.py --tally <this file>`.",
        "",
        "---",
        "",
    ]

    for trial_dir, result in sample:
        task_meta = read_task_meta(tasks_dir / result["task_id"]) if (
            tasks_dir / result["task_id"] / "task.toml"
        ).exists() else {}
        raw = result.get("verifier_raw") or {}
        lines += [
            f"## {result['task_id']}",
            "",
            f"pattern `{task_meta.get('task_pattern', '?')}` · "
            f"difficulty `{task_meta.get('difficulty', '?')}` · "
            f"objective `{task_meta.get('objective_kind', '?')}`",
            "",
            f"reward **{result.get('reward')}** · terminal `{result.get('terminal_reason')}` · "
            f"steps {result.get('steps')} · "
            f"tokens in/out {result.get('tokens', {}).get('input')}/{result.get('tokens', {}).get('output')} · "
            f"${result.get('cost_usd')}",
            "",
            f"constraint {(raw.get('constraint') or {}).get('earned')}/{(raw.get('constraint') or {}).get('total')} · "
            f"hygiene {(raw.get('hygiene') or {}).get('earned')}/{(raw.get('hygiene') or {}).get('total')} · "
            f"optimality {(raw.get('optimality') or {}).get('score')}",
            "",
            "Failed rules:",
            "",
        ]
        rules = failed_rules(raw)
        lines += [f"- `{rule}`" for rule in rules] or ["- (none reported)"]
        lines += ["", "Trajectory:", "", "```"]
        trajectory = trial_dir / "trajectory.jsonl"
        lines += condense(trajectory, head, tail, width) if trajectory.exists() else ["  (no trajectory)"]
        lines += ["```", "", "**Code:** ", "", "**Note:** ", "", "---", ""]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n")
    print(f"wrote {out_path} — {len(sample)} trials to code")
    return len(sample)


def tally(path: Path) -> int:
    codes = Counter()
    uncoded = 0
    for match in re.finditer(r"^\*\*Code:\*\*\s*(.*)$", path.read_text(), re.M):
        code = match.group(1).strip().upper()
        if not code:
            uncoded += 1
        else:
            codes[code.split()[0]] += 1
    total = sum(codes.values())
    print(f"{path.name}: {total} coded, {uncoded} uncoded")
    for code, count in codes.most_common():
        marker = "" if code in TAXONOMY else "  <-- not in taxonomy"
        share = f"{100 * count / total:.0f}%" if total else "-"
        print(f"  {code:<18}{count:>4}  {share:>5}{marker}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", nargs="?", help="a run directory under runs/")
    parser.add_argument("--out", help="markdown file to write")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--head", type=int, default=12, help="events shown from the start")
    parser.add_argument("--tail", type=int, default=24, help="events shown from the end")
    parser.add_argument("--width", type=int, default=220, help="characters per event")
    parser.add_argument("--tasks-dir", default=str(REPO_ROOT / "vendor/erp-bench/tasks"))
    parser.add_argument("--tally", help="count the codes in an already-coded report")
    args = parser.parse_args()

    if args.tally:
        return tally(Path(args.tally))
    if not args.run_dir:
        parser.error("run_dir is required unless --tally is given")

    run_dir = Path(args.run_dir)
    out_path = Path(args.out) if args.out else REPO_ROOT / "analysis" / f"failures_{run_dir.name}.md"
    write_report(
        run_dir, out_path, args.limit, args.seed, args.head, args.tail, args.width,
        Path(args.tasks_dir),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
