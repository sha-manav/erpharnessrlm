#!/usr/bin/env python3
"""Paired comparison of a dev40 run against the config-A baseline, task by task.

Written before the C_full dev40 run started, so the stopping rule is fixed in advance:

    after >= 16 paired tasks, if C's paired passes <= A's paired passes - 4,
    STOP the run and diagnose rather than spend the remainder.

Pairing matters more than the headline mean while a run is partial: the tasks that finish
first skew easy, so "C is at 50%" means nothing until you know what A scored on *those*
tasks. This script only ever compares on the intersection.

    python3 scripts/paired.py runs/C_full__big__dev40__...   [--baseline runs/A_pi__big__dev40__...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASELINE = REPO_ROOT / "runs" / "A_pi__big__dev40__20260902T002458Z"

MIN_PAIRED = 16
STOP_MARGIN = 4


def load(run_dir: Path) -> dict[str, dict]:
    out = {}
    # Prefer ingested results; fall back to Harbor's raw reward files for a live run.
    for path in run_dir.glob("*/result.json"):
        r = json.loads(path.read_text())
        if r.get("reward") is not None:
            out[r["task_id"]] = {"pass": bool(r["pass"]), "reward": r["reward"],
                                 "steps": r.get("steps"), "cost": r.get("cost_usd"),
                                 "terminal": r.get("terminal_reason")}
    if not out:
        for path in run_dir.glob("jobs/*/*/verifier/reward.json"):
            d = json.loads(path.read_text())
            result = json.loads((path.parent.parent / "result.json").read_text())
            task = (result.get("task_name") or "").split("/")[-1]
            if task:
                out[task] = {"pass": bool(d.get("passed")), "reward": d.get("overall_score", 0.0),
                             "steps": None, "cost": None, "terminal": "?"}
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir")
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    args = parser.parse_args()

    cand = load(Path(args.run_dir))
    base = load(Path(args.baseline))
    paired = sorted(set(cand) & set(base))
    if not paired:
        print("no paired tasks yet")
        return 0

    a_pass = sum(base[t]["pass"] for t in paired)
    c_pass = sum(cand[t]["pass"] for t in paired)
    a_rew = sum(base[t]["reward"] for t in paired) / len(paired)
    c_rew = sum(cand[t]["reward"] for t in paired) / len(paired)

    print(f"paired tasks: {len(paired)}")
    print(f"  A_pi   pass {a_pass:>2}/{len(paired)}  mean reward {a_rew:5.1f}")
    print(f"  C_full pass {c_pass:>2}/{len(paired)}  mean reward {c_rew:5.1f}")
    wins = [t for t in paired if cand[t]["pass"] and not base[t]["pass"]]
    losses = [t for t in paired if base[t]["pass"] and not cand[t]["pass"]]
    print(f"  C wins where A lost: {len(wins)}  |  C loses where A won: {len(losses)}")
    for t in losses:
        print(f"     lost: {t[:52]}  C reward {cand[t]['reward']:.1f}  ({cand[t]['terminal']})")

    costs = [cand[t]["cost"] for t in paired if cand[t].get("cost") is not None]
    if costs:
        print(f"  C mean cost ${sum(costs)/len(costs):.3f}/task")

    if len(paired) >= MIN_PAIRED and c_pass <= a_pass - STOP_MARGIN:
        print(f"\nSTOP RULE TRIGGERED: {c_pass} <= {a_pass} - {STOP_MARGIN} on {len(paired)} paired tasks")
        return 2
    verdict = "ahead" if c_pass > a_pass else "level" if c_pass == a_pass else "behind"
    print(f"\nC is {verdict} on the paired set ({'stop rule not yet applicable' if len(paired) < MIN_PAIRED else 'stop rule not triggered'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
