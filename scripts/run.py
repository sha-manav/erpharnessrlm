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
EVAL_SETS = {"eval100", "eval100_remaining", "eval100_remaining_big", "eval100_remaining_small"}

# Sets used purely for iteration while building the harness. A trial costs ~$0.73 on the
# big model and ~$0.10 on the small one, and nothing from these sets goes into the
# write-up, so they default to the small model (PLAN.md P2.9: only dev40 feeds the dev
# curve). Using the big model here is allowed but has to be deliberate.
ITERATION_SETS = {"dev5", "dev10", "make6", "make7", "smoke4"}


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


def openrouter_balance(api_key: str) -> float | None:
    """Remaining OpenRouter credit in USD, or None if it cannot be determined."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/credits",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read()).get("data", {})
    except (urllib.error.URLError, ValueError, TimeoutError):
        return None
    total = data.get("total_credits")
    used = data.get("total_usage")
    if total is None or used is None:
        return None
    return float(total) - float(used)


def preflight_model(model: dict, api_key: str) -> dict:
    """Send one tiny request and confirm the model actually answers for this account.

    Learned the hard way twice over. A run whose every trial dies on its first model call
    still takes an hour of machine time and still writes 100 plausible-looking results:
    the config-A small-model run returned pass@1 0.0 because an account-level provider
    restriction left `qwen/qwen3-32b` with no allowed endpoint
    ("No endpoints available matching your guardrail restrictions", HTTP 404). Five
    seconds here would have caught it.

    Returns the serving provider so meta.json records which upstream a run actually used.
    """
    import urllib.error
    import urllib.request

    payload = {
        "model": model["model"],
        "max_tokens": 4,
        "messages": [{"role": "user", "content": "ping"}],
    }
    if model.get("provider_routing"):
        payload["provider"] = model["provider_routing"]   # the pin the real requests carry
    request = urllib.request.Request(
        f"{model['base_url'].rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise SystemExit(
            f"refusing to start: {model['model']} is not answering for this account "
            f"(HTTP {exc.code}): {detail}"
        ) from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SystemExit(f"refusing to start: cannot reach {model['base_url']}: {exc}") from None

    if body.get("error"):
        raise SystemExit(f"refusing to start: {model['model']} returned {body['error']}")
    provider = body.get("provider")
    print(f"model check: {model['model']} answered via {provider or 'unknown provider'}")
    return {"reachable": True, "provider": provider}


def preflight_credit(model: dict, api_key: str, n_tasks: int, allow_low: bool,
                     n_concurrent: int = 1) -> dict:
    """Refuse to start a run the account cannot pay for.

    Learned the hard way: a run that exhausts its balance mid-flight does not fail loudly.
    Every remaining trial returns HTTP 402 before its first model call, lands in the results
    with one step and $0 spent, and the aggregate reports a plausible-looking low pass rate
    that is really a billing artefact.

    OpenRouter reserves credit for the *requested* `max_tokens`, not for what is used — pi
    asks for ~180k tokens per call — so a balance that looks adequate in expectation can
    still be rejected. Hence the floor on top of the estimate.
    """
    if "openrouter.ai" not in model.get("base_url", ""):
        return {"checked": False}

    balance = openrouter_balance(api_key)
    per_trial = model.get("est_cost_per_trial_usd", 0.35)
    needed = n_tasks * per_trial
    # OpenRouter holds a reservation per *in-flight* request against its maximum possible
    # size, so a run can be refused with HTTP 402 while its expected spend is affordable.
    # Two dev40 runs at 18 concurrent burned ~$50 and returned 32% and 55% api_error for
    # exactly this reason. Budget for the concurrency, not only for the mean.
    in_flight = n_concurrent * model.get("in_flight_reservation_usd", 2.0)
    floor = 5.0
    report = {
        "checked": True,
        "balance_usd": balance,
        "estimated_usd": round(needed, 2),
        "in_flight_reserve_usd": round(in_flight, 2),
        "required_usd": round(max(needed * 1.2, needed + floor, needed + in_flight), 2),
    }
    if balance is None:
        print("warning: could not read the OpenRouter balance; continuing unchecked")
        return report
    print(f"credit: ${balance:.2f} available, ~${needed:.2f} estimated for {n_tasks} "
          f"trials, ~${in_flight:.2f} held for {n_concurrent} in flight")
    if balance < report["required_usd"] and not allow_low:
        raise SystemExit(
            f"refusing to start: ${balance:.2f} available but ${report['required_usd']:.2f} "
            f"needed for {n_tasks} trials at ~${per_trial:.2f} each, including "
            f"~${in_flight:.2f} held against {n_concurrent} in-flight requests "
            "(OpenRouter reserves per request, so concurrency needs headroom of its own).\n"
            "Add credits at https://openrouter.ai/settings/credits, or pass --allow-low-credit "
            "to run anyway and accept that trials may die with HTTP 402."
        )
    return report


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
    parser.add_argument(
        "--alias",
        help=(
            "label this run under a different set name in the run directory. Used to run a "
            "frozen set in disjoint batches (e.g. --set eval100_remaining --alias eval100) so "
            "the batches aggregate as one set. Only legitimate when the batches are disjoint "
            "and were not selected on outcome; meta.json records both names."
        ),
    )
    parser.add_argument("-n", "--n-concurrent", type=int, default=int(os.environ.get("ERP_N", "4")))
    parser.add_argument("--tasks-dir", default=str(REPO_ROOT / "vendor/erp-bench/tasks"))
    parser.add_argument("--background", action="store_true", help="detach and return immediately")
    parser.add_argument("--allow-dirty", action="store_true", help="permit an eval run from a dirty tree")
    parser.add_argument("--dry-run", action="store_true", help="print the command and exit")
    parser.add_argument("--allow-low-credit", action="store_true",
                        help="start even if the provider balance looks too small")
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

    env_file = load_env(REPO_ROOT / ".env")
    api_key = env_file.get(model["api_key_env"]) or os.environ.get(model["api_key_env"], "")
    if not api_key:
        raise SystemExit(f"{model['api_key_env']} is not set (looked in .env and the environment)")
    if args.set in ITERATION_SETS and args.model == "big":
        print(
            f"note: '{args.set}' is an iteration set and you asked for the big model "
            f"(~${model.get('est_cost_per_trial_usd', 0):.2f}/trial vs "
            f"~${models['small'].get('est_cost_per_trial_usd', 0):.2f} on small). "
            "Only dev40 needs the big model."
        )
    reachability = preflight_model(model, api_key)
    credit = preflight_credit(model, api_key, len(task_ids), args.allow_low_credit,
                              args.n_concurrent)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{args.config}__{args.model}__{args.alias or args.set}__{stamp}"
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
        "set_alias": args.alias,
        "n_tasks": len(task_ids),
        "n_concurrent": args.n_concurrent,
        "commit": git("rev-parse", "HEAD"),
        "tag": git("describe", "--tags", "--exact-match") or None,
        "dirty": dirty,
        "seed": 0,
        "harbor_version": subprocess.run(["harbor", "--version"], capture_output=True)
        .stdout.decode().strip(),
        "command": " ".join(shlex.quote(part) for part in cmd),
        "credit_preflight": credit,
        "model_preflight": reachability,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "terminal_reasons": {},
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    print(f"run_dir: {run_dir}")
    print(f"command: {meta['command']}")
    if args.dry_run:
        return 0

    env = {**os.environ, **env_file}
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
