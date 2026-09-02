#!/usr/bin/env python3
"""Append a row to analysis/dev_curve.csv for one dev-set run (P2.9, P3.x).

The dev curve is the only place a harness change is allowed to be judged: one row per
(harness version, config), always on the same 40 dev tasks, so that "did this help?" is
answered by a number rather than by a hunch about a trajectory. PLAN.md keeps an addition
only if mean reward does not drop and either reward improves or tokens fall — both columns
are here for that reason.

    python3 scripts/dev_curve.py runs/C_full__big__dev40__... --version v0

`token_cap_hits` is carried explicitly: a configuration that "improves" by spending its
way to the cap is not an improvement, and the count is the cheapest way to see it.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = REPO_ROOT / "analysis" / "dev_curve.csv"

COLUMNS = [
    "version", "config", "model", "set", "n", "pass_at_1", "mean_reward", "mean_steps",
    "mean_tokens_in", "mean_tokens_cached", "mean_tokens_out", "mean_cost_usd",
    "token_cap_hits", "step_cap_hits", "loop_hits", "api_error_hits", "crash_hits",
    "run_id", "commit",
]


def summarise(run_dir: Path, version: str) -> dict:
    results = []
    for path in sorted(run_dir.glob("*/result.json")):
        result = json.loads(path.read_text())
        if result.get("reward") is None:
            continue
        results.append(result)
    if not results:
        raise SystemExit(f"{run_dir} has no completed trials — ingest it first")

    meta = json.loads((run_dir / "meta.json").read_text()) if (run_dir / "meta.json").exists() else {}
    n = len(results)
    reasons = Counter(r.get("terminal_reason") for r in results)

    def mean(key, sub=None):
        values = [(r[key][sub] if sub else r[key]) for r in results if r.get(key) is not None]
        return sum(values) / len(values) if values else 0.0

    return {
        "version": version,
        "config": results[0].get("config"),
        "model": results[0].get("model"),
        "set": meta.get("set_alias") or meta.get("set") or "",
        "n": n,
        "pass_at_1": round(100 * sum(1 for r in results if r.get("pass")) / n, 1),
        "mean_reward": round(mean("reward"), 1),
        "mean_steps": round(mean("steps"), 1),
        "mean_tokens_in": round(mean("tokens", "input")),
        "mean_tokens_cached": round(mean("tokens", "cached")),
        "mean_tokens_out": round(mean("tokens", "output")),
        "mean_cost_usd": round(mean("cost_usd"), 3),
        "token_cap_hits": reasons.get("token_cap", 0),
        "step_cap_hits": reasons.get("step_cap", 0),
        "loop_hits": reasons.get("loop", 0),
        "api_error_hits": reasons.get("api_error", 0),
        "crash_hits": reasons.get("crash", 0) + reasons.get("timeout", 0),
        "run_id": run_dir.name,
        "commit": (meta.get("commit") or "")[:8],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dirs", nargs="+")
    parser.add_argument("--version", required=True, help="harness version label, e.g. v0")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if out_path.exists():
        with out_path.open() as handle:
            existing = [row for row in csv.DictReader(handle)]

    rows = [summarise(Path(d), args.version) for d in args.run_dirs]
    # A rerun of the same (version, config, run) replaces its row rather than duplicating.
    keys = {(r["version"], r["config"], r["run_id"]) for r in rows}
    existing = [r for r in existing if (r["version"], r["config"], r["run_id"]) not in keys]

    with out_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(existing + rows)

    print(f"wrote {out_path} ({len(existing) + len(rows)} rows)")
    header = f"{'version':<8}{'config':<12}{'n':>4}{'pass@1':>8}{'reward':>8}{'steps':>7}" \
             f"{'tok_in':>10}{'cached':>10}{'$':>8}  caps"
    print(header)
    for row in existing + rows:
        caps = (f"token:{row['token_cap_hits']} step:{row['step_cap_hits']} "
                f"loop:{row['loop_hits']} err:{row['api_error_hits']}")
        print(f"{row['version']:<8}{str(row['config']):<12}{str(row['n']):>4}"
              f"{str(row['pass_at_1']):>8}{str(row['mean_reward']):>8}{str(row['mean_steps']):>7}"
              f"{int(row['mean_tokens_in']):>10,}{int(row['mean_tokens_cached']):>10,}"
              f"{float(row['mean_cost_usd']):>8.3f}  {caps}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
