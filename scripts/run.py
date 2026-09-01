#!/usr/bin/env python3
"""Launch one (config, model, task set) run and record everything needed to trust it.

    python3 scripts/run.py --config A_pi --model big --set eval100 [-n 6] [--background]

Every run gets its own directory under `runs/`:

    runs/<config>__<model>__<set>__<UTC timestamp>/
      meta.json     git commit + dirty flag, config, model, caps, seed, timings,
                    per-task terminal reason (filled in by scripts/ingest_harbor.py)
      stdout.log    harbor's console output
      jobs/         harbor's own job directory (trials, trajectories, rewards)

PLAN.md hard rule 4 forbids eval runs from a dirty tree or an untagged commit, so an
eval-set run refuses unless the tree is clean (override with --allow-dirty only for
debugging, which the meta.json then records).
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL_SETS = {"eval100"}


def git(*args: str) -> str:
    proc = subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True)
    return proc.stdout.decode().strip()


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def resolve_config(configs: dict, name: str) -> dict:
    if name not in configs:
        raise SystemExit(f"unknown config '{name}'; have {sorted(k for k in configs if k != 'defaults')}")
    resolved = dict(configs.get("defaults", {}))
    spec = configs[name]
    parent = spec.get("inherit")
    if parent:
        resolved.update({k: v for k, v in resolve_config(configs, parent).items()})
    resolved.update({k: v for k, v in spec.items() if k != "inherit"})
    resolved["id"] = name
    return resolved


def read_task_list(path: Path) -> list[str]:
    ids = [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not ids:
        raise SystemExit(f"{path} lists no task ids")
    return ids


def build_command(
    config: dict, model: dict, model_name: str, task_ids: list[str], run_dir: Path,
    tasks_dir: Path, n_concurrent: int, extra: list[str],
) -> list[str]:
    cmd = [
        "harbor", "run",
        "-p", str(tasks_dir),
        "--env", "docker",
        "-o", str(run_dir / "jobs"),
        "--job-name", config["id"],
        "-n", str(n_concurrent),
        "-y",
        "--verifier", "harness.verifier:FlatVerifier",
        "--env-file", str(REPO_ROOT / ".env"),
    ]
    for task_id in task_ids:
        cmd += ["-i", task_id]

    if config.get("runner") == "pi":
        cmd += ["-a", "pi", "-m", model["pi_model"]]
        if config.get("pi_version"):
            cmd += ["--ak", f"version={config['pi_version']}"]
    else:
        cmd += ["-a", "harness.agent:ErpAgent", "-m", model["model"]]
        cmd += ["--ak", f"config={config['id']}"]
        cmd += ["--ak", f"model_key={model_name}"]
    return cmd + extra


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--model", required=True, choices=["big", "small"])
    parser.add_argument("--set", required=True, help="a name under configs/, e.g. dev5 or eval100")
    parser.add_argument("-n", "--n-concurrent", type=int, default=int(os.environ.get("ERP_N", "4")))
    parser.add_argument("--tasks-dir", default=str(REPO_ROOT / "vendor/erp-bench/tasks"))
    parser.add_argument("--background", action="store_true", help="detach and return immediately")
    parser.add_argument("--allow-dirty", action="store_true", help="permit an eval run from a dirty tree")
    parser.add_argument("--dry-run", action="store_true", help="print the command and exit")
    parser.add_argument("extra", nargs="*", help="extra flags passed through to harbor")
    args = parser.parse_args()

    configs = yaml.safe_load((REPO_ROOT / "configs/configs.yaml").read_text())
    models = yaml.safe_load((REPO_ROOT / "configs/models.yaml").read_text())
    config = resolve_config(configs, args.config)
    model = models[args.model]

    task_ids = read_task_list(REPO_ROOT / "configs" / f"{args.set}.txt")

    dirty = bool(git("status", "--porcelain"))
    if args.set in EVAL_SETS and dirty and not args.allow_dirty:
        raise SystemExit(
            "refusing to start an eval run from a dirty tree (PLAN.md hard rule 4). "
            "Commit first, or pass --allow-dirty for a debug run."
        )

    guard = subprocess.run(["bash", str(REPO_ROOT / "scripts/guard.sh")], capture_output=True)
    if guard.returncode != 0:
        sys.stderr.write(guard.stderr.decode())
        raise SystemExit("guard failed; not starting a run")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{args.config}__{args.model}__{args.set}__{stamp}"
    run_dir = REPO_ROOT / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    cmd = build_command(
        config, model, args.model, task_ids, run_dir,
        Path(args.tasks_dir), args.n_concurrent, args.extra,
    )

    meta = {
        "run_id": run_id,
        "config": config,
        "model_key": args.model,
        "model": {k: v for k, v in model.items() if "key" not in k},
        "set": args.set,
        "n_tasks": len(task_ids),
        "n_concurrent": args.n_concurrent,
        "commit": git("rev-parse", "HEAD"),
        "tag": git("describe", "--tags", "--exact-match") or None,
        "dirty": dirty,
        "seed": 0,
        "harbor_version": subprocess.run(["harbor", "--version"], capture_output=True)
        .stdout.decode().strip(),
        "command": " ".join(shlex.quote(part) for part in cmd),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "terminal_reasons": {},
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    print(f"run_dir: {run_dir}")
    print(f"command: {meta['command']}")
    if args.dry_run:
        return 0

    env = {**os.environ, **load_env(REPO_ROOT / ".env")}
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PATH"] = f"{Path.home()}/.local/bin:{env.get('PATH', '')}"

    log_path = run_dir / "stdout.log"
    if args.background:
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                cmd, stdout=log, stderr=subprocess.STDOUT, env=env,
                cwd=REPO_ROOT, start_new_session=True,
            )
        print(f"started in background: pid {process.pid}  (tail {log_path})")
        return 0

    with log_path.open("wb") as log:
        process = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=env, cwd=REPO_ROOT)
    meta["finished_at"] = datetime.now(timezone.utc).isoformat()
    meta["harbor_return_code"] = process.returncode
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    print(f"finished rc={process.returncode}; see {log_path}")
    return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
