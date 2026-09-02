#!/usr/bin/env python3
"""Score a dev run against the pre-registered checkpoint criteria (P2.9 gate rehearsal).

Written before the checkpoint run finished, so the bar is fixed in advance rather than
fitted to the result. The criteria are the four things that had to change after the first
C_full dev40 run, each with the measurement that motivated it:

    cache hit rate  >= 60%   (was 31%: the breakpoint never moved off the system block)
    refusals        >= 1 and every refusal resolved  (was 0 of 19: the gate could not see
                              the 74% failure mode)
    NameErrors      == 0     (was 31: the contract named modules the config did not ship)
    api_errors      == 0     (was 32.5%: transient 402s treated as fatal)

It also prints, verbatim, what the model did in the turn AFTER each refusal -- the single
observation that decides whether the gate helps or merely costs a step.

    python3 scripts/checkpoint.py runs/C_full__big__dev5__...
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

CRITERIA = {"cache_pct": 60.0, "min_refusals": 1, "name_errors": 0, "api_errors": 0}


def load(run_dir: Path):
    trials = []
    for result_path in sorted(run_dir.glob("*/result.json")):
        result = json.loads(result_path.read_text())
        if result.get("reward") is None:
            continue
        traj = result_path.parent / "trajectory.jsonl"
        events = [json.loads(l) for l in traj.read_text().splitlines()] if traj.exists() else []
        trials.append((result, events))
    return trials


def score(run_dir: Path, show_turns: int = 2) -> bool:
    trials = load(run_dir)
    if not trials:
        print("no completed trials")
        return False

    tin = sum(r["tokens"]["input"] for r, _ in trials)
    cached = sum(r["tokens"]["cached"] for r, _ in trials)
    cache_pct = 100 * cached / tin if tin else 0.0

    refusals = resolved = name_errors = 0
    api_errors = sum(1 for r, _ in trials if r["terminal_reason"] in ("api_error", "crash"))
    transport = 0
    # Added before pass 2 ran, after pass 1 exposed them: the agent validating receipts on
    # day 0, and writing a date_planned the vendor's lead time cannot meet. Both are now
    # refused by timeline_feasible; counting the *attempts* shows whether the playbook
    # change stopped the behaviour or only the gate is catching it.
    early_receives = wishful_dates = 0
    post_refusal: list[tuple[str, int, list[str]]] = []

    for result, events in trials:
        tool_events = [e for e in events if e["role"] == "tool"]
        for i, event in enumerate(tool_events):
            out = event.get("output") or ""
            name_errors += len(re.findall(r"NameError: name '\w+' is not defined", out))
            if "kernel transport failed" in out or "Docker compose cp" in out:
                transport += 1
            code = (event.get("args") or {}).get("code") or ""
            if "receive(" in code and "erp.receive" in code:
                early_receives += 1
            if "lead time" in out and "makes it" in out:
                wishful_dates += 1
            if event.get("tool") == "finish" and "finish refused" in out:
                refusals += 1
                # Resolved if a later finish in the same trial carried the sentinel.
                later = [e for e in tool_events[i + 1:] if e.get("tool") == "finish"]
                if any("__ERP_HARNESS_FINISHED__" in (e.get("output") or "") for e in later):
                    resolved += 1
                nxt = []
                for e in tool_events[i + 1:i + 1 + show_turns]:
                    code = (e.get("args") or {}).get("code") or (e.get("args") or {}).get("summary") or ""
                    nxt.append(f"    -> {e.get('tool')}: {code[:260].replace(chr(10), ' | ')}")
                post_refusal.append((result["task_id"], event["t"], nxt))

    n = len(trials)
    passes = sum(1 for r, _ in trials if r["pass"])
    print(f"run: {run_dir.name}")
    print(f"trials {n}  pass {passes}/{n}  mean reward {sum(r['reward'] for r, _ in trials)/n:.1f}  "
          f"mean steps {sum(r['steps'] for r, _ in trials)/n:.1f}  "
          f"mean $ {sum(r['cost_usd'] for r, _ in trials)/n:.3f}")
    print(f"terminal: { {r['terminal_reason'] for r, _ in trials} }")
    print()
    checks = [
        ("cache hit rate", f"{cache_pct:.0f}%", cache_pct >= CRITERIA["cache_pct"], f">= {CRITERIA['cache_pct']:.0f}%"),
        ("refusals", f"{refusals} ({resolved} resolved)",
         refusals >= CRITERIA["min_refusals"] and resolved == refusals, ">= 1, all resolved"),
        ("NameErrors", str(name_errors), name_errors == CRITERIA["name_errors"], "== 0"),
        ("api_errors/crashes", str(api_errors), api_errors == CRITERIA["api_errors"], "== 0"),
        ("transport failures", str(transport), transport == 0, "== 0 (informational)"),
        ("early receive() calls", str(early_receives), True, "informational: playbook now says don't"),
        ("wishful date_planned caught", str(wishful_dates), True, "informational: gate now sees it"),
    ]
    all_ok = True
    for label, value, ok, bar in checks:
        mark = "PASS" if ok else "FAIL"
        if label in ("cache hit rate", "refusals", "NameErrors", "api_errors/crashes"):
            all_ok &= ok
        print(f"  [{mark}] {label:<20} {value:<18} bar {bar}")

    if post_refusal:
        print("\nWhat the model did after each refusal:")
        for task, step, lines in post_refusal:
            print(f"  {task[:44]} @ step {step}")
            for line in lines:
                print(line)
    print("\nCHECKPOINT", "PASSED" if all_ok else "FAILED")
    return all_ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir")
    parser.add_argument("--turns", type=int, default=2, help="post-refusal turns to print")
    args = parser.parse_args()
    return 0 if score(Path(args.run_dir), args.turns) else 1


if __name__ == "__main__":
    sys.exit(main())
