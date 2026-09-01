#!/usr/bin/env python3
"""Package a completed set of runs for the repository (`make publish`).

`runs/` is gitignored — a single eval100 run is ~180 MB of Harbor's own output — but the
parts that constitute the *result* are small and belong in version control:

    analysis/results.csv                      one row per (task, config, model)
    analysis/reproduction.md                  the P1.3 comparison against the paper
    analysis/trajectories/<run_id>.tar.gz     normalised trajectories, a few MB per run

Invalid runs (marked in their meta.json) are excluded everywhere, so what is published is
only what counts.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tarfile
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.discover import read_task_meta  # noqa: E402

# Published coding-harness pass@1, Anchor arXiv:2605.26321 Appendix G.1 Table 12.
PUBLISHED = {"z-ai/glm-5.1": 35.8}
TOLERANCE = 8.0


def valid_runs(runs_dir: Path) -> list[Path]:
    out = []
    for run_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        meta_path = run_dir / "meta.json"
        if not meta_path.exists():
            continue
        if json.loads(meta_path.read_text()).get("invalid"):
            continue
        out.append(run_dir)
    return out


def load_results(run_dirs: list[Path]) -> dict[tuple, dict]:
    """(config, model, task) -> result. Later runs win, so a rerun supersedes."""
    merged: dict[tuple, dict] = {}
    for run_dir in run_dirs:
        for path in sorted(run_dir.glob("*/result.json")):
            result = json.loads(path.read_text())
            if result.get("reward") is None:
                continue
            merged[(result["config"], result.get("model_key"), result["task_id"])] = result
    return merged


def write_reproduction(results: dict[tuple, dict], tasks_dir: Path, out: Path) -> None:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for (config, model_key, _), result in results.items():
        groups[(config, model_key)].append(result)

    lines = [
        "# Reproduction of the published ERP-Bench numbers (P1.3)",
        "",
        "Config A is Harbor's built-in `pi` agent (pinned to npm 0.84.4), the same generic",
        "coding-agent harness the Anchor paper used, on the frozen 100-task subset.",
        "",
        "| config | model | n | pass@1 | published | Δ | mean reward | mean steps | $/task |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    verdicts = []
    for (config, model_key), rows in sorted(groups.items(), key=lambda kv: str(kv[0])):
        n = len(rows)
        passes = [r for r in rows if r.get("pass")]
        pass_rate = 100 * len(passes) / n
        model = rows[0].get("model", "")
        published = PUBLISHED.get(model)
        delta = f"{pass_rate - published:+.1f}" if published else "—"
        lines.append(
            f"| {config} | {model} | {n} | {pass_rate:.1f} | "
            f"{published if published else '—'} | {delta} | "
            f"{sum(r['reward'] for r in rows) / n:.1f} | "
            f"{sum(r['steps'] for r in rows) / n:.1f} | "
            f"{sum(r['cost_usd'] for r in rows) / n:.3f} |"
        )
        if published is not None:
            ok = abs(pass_rate - published) <= TOLERANCE
            verdicts.append(
                f"- **{config} / {model}**: {pass_rate:.1f} against {published} published, "
                f"a gap of {abs(pass_rate - published):.1f} points — "
                f"{'within' if ok else 'OUTSIDE'} the ±{TOLERANCE:g}-point tolerance."
            )

    lines += ["", "## Verdict", ""] + (verdicts or ["- no model with a published number"])
    lines += [
        "",
        "## What differs from the published setup",
        "",
        "| | Anchor Table 12 | here |",
        "|---|---|---|",
        "| tasks | all 300 | frozen 100, stratified over all 29 patterns |",
        "| trials per task | 5 | 1 |",
        "| harness | pi-mono toolkit | pi 0.84.4 (`@earendil-works/pi-coding-agent`), same CLI and tools |",
        "| turn budget | 400 | none enforced — pi 0.84.4 has no `--max-turns` |",
        "| timeout | 1 h | 1 h (`[agent] timeout_sec = 3600`) |",
        "",
        "Sampling noise alone puts a 1-trial, 100-task estimate of a ~36% rate at about",
        "±4.8 points (1 SE), so agreement inside a couple of points is as close as this",
        "design can resolve.",
        "",
        "## Per-pattern breakdown",
        "",
        "| pattern | n | pass@1 |",
        "|---|---:|---:|",
    ]

    by_pattern: dict[str, list[dict]] = defaultdict(list)
    for (config, model_key, task_id), result in results.items():
        if config != "A_pi" or model_key != "big":
            continue
        meta = read_task_meta(tasks_dir / task_id) if (tasks_dir / task_id).exists() else {}
        by_pattern[meta.get("task_pattern", "?")].append(result)
    for pattern, rows in sorted(by_pattern.items()):
        rate = 100 * sum(1 for r in rows if r.get("pass")) / len(rows)
        lines.append(f"| {pattern} | {len(rows)} | {rate:.0f} |")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {out}")


def pack_trajectories(run_dirs: list[Path], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for run_dir in run_dirs:
        members = sorted(run_dir.glob("*/trajectory.jsonl")) + sorted(run_dir.glob("*/result.json"))
        if not members:
            continue
        archive = out_dir / f"{run_dir.name}.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(run_dir / "meta.json", arcname=f"{run_dir.name}/meta.json")
            for path in members:
                tar.add(path, arcname=f"{run_dir.name}/{path.parent.name}/{path.name}")
        size = archive.stat().st_size / 1e6
        print(f"packed {archive.name} ({len(members)} files, {size:.1f} MB)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", default=str(REPO_ROOT / "runs"))
    parser.add_argument("--tasks-dir", default=str(REPO_ROOT / "vendor/erp-bench/tasks"))
    parser.add_argument("--no-trajectories", action="store_true")
    args = parser.parse_args()

    run_dirs = valid_runs(Path(args.runs))
    print(f"{len(run_dirs)} valid run(s): {[r.name for r in run_dirs]}")
    results = load_results(run_dirs)
    print(f"{len(results)} unique (config, model, task) results")

    subprocess.run([sys.executable, str(REPO_ROOT / "scripts/aggregate.py")], check=True)
    write_reproduction(results, Path(args.tasks_dir), REPO_ROOT / "analysis/reproduction.md")
    if not args.no_trajectories:
        pack_trajectories(run_dirs, REPO_ROOT / "analysis/trajectories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
