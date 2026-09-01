"""Harbor entrypoint: wires the container, kernel, prompts and loop together (PLAN.md P2.8).

Harbor instantiates a custom agent **on the host** and hands it an async `BaseEnvironment`
(NOTES.md ## Harbor). This class is that agent. Per trial it:

1. waits for the task's Odoo to finish loading its scenario (`/tmp/saas_setup_complete`);
2. copies `kernel_server.py` and the config's subset of `lib/` into the container and
   starts the kernel with the Odoo/Postgres environment exported;
3. assembles the system prompt from the config's prompt files;
4. runs the loop, dispatching `python` to the kernel, `bash` to the container, `show` to
   the page store and `finish` into the kernel so the check gate runs against live Odoo;
5. writes `trajectory.jsonl` and `result.json` into Harbor's agent log directory and
   populates `AgentContext` so Harbor's own accounting agrees with ours.

The loop is synchronous and Harbor's `run` is async, so the loop is parked on a worker
thread and `HarborContainer` hops back to the event loop for each exec.

Configuration comes entirely from `configs/configs.yaml` via `--ak config=<id>`: which
tools exist, which prompts are concatenated, which lib modules ship, whether the finish
gate and the ledger are on. No behaviour is keyed to a task or a pattern.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from harness.container import HarborContainer, Kernel
from harness.llm import LLM
from harness.loop import Loop, Trajectory
from harness.tools import schemas_for

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPT_DIR = REPO_ROOT / "harness" / "prompts"

# Exported into the kernel so lib/erp.py can reach Odoo and Postgres without the agent
# having to discover them. Values are the ERP-Bench image's (NOTES.md ## ERP-Bench).
CONTAINER_ENV = {
    "ODOO_URL": "http://127.0.0.1:8069",
    "ODOO_DB": "bench",
    "ODOO_USER": "admin",
    "PGHOST": "127.0.0.1",
    "PGPORT": "5432",
    "PGUSER": "odoo",
    "PGPASSWORD": "odoo",
    "PGDATABASE": "bench",
}

SETUP_MARKER = "/tmp/saas_setup_complete"
SETUP_TIMEOUT_S = 600


class ErpAgent(BaseAgent):
    SUPPORTS_ATIF = False

    def __init__(self, logs_dir: Path, model_name: str | None = None, logger=None,
                 config: str = "C_full", model_key: str = "big", **kwargs):
        super().__init__(logs_dir=logs_dir, model_name=model_name, logger=logger, **kwargs)
        self.config_id = config
        self.model_key = model_key
        self.configs = yaml.safe_load((REPO_ROOT / "configs/configs.yaml").read_text())
        self.models = yaml.safe_load((REPO_ROOT / "configs/models.yaml").read_text())
        self.config = self._resolve(config)
        self.lib_active: list[str] = []
        self.result: dict[str, Any] = {}

    # -- configuration --------------------------------------------------------
    def _resolve(self, name: str) -> dict:
        if name not in self.configs:
            raise ValueError(f"unknown config {name!r}")
        resolved = dict(self.configs.get("defaults", {}))
        spec = self.configs[name]
        if spec.get("inherit"):
            resolved.update(self._resolve(spec["inherit"]))
        resolved.update({k: v for k, v in spec.items() if k != "inherit"})
        resolved["id"] = name
        return resolved

    def _system_prompt(self) -> str:
        parts = []
        for name in self.config.get("prompts", []):
            path = PROMPT_DIR / f"{name}.md"
            if not path.exists():
                self.logger.warning("prompt %s missing; skipping", path)
                continue
            parts.append(path.read_text().strip())
        return "\n\n---\n\n".join(parts)

    @staticmethod
    def name() -> str:
        return "erp-harness"

    def version(self) -> str:
        return "0.1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        return

    # -- lifecycle ------------------------------------------------------------
    async def run(self, instruction: str, environment: BaseEnvironment,
                  context: AgentContext) -> None:
        loop = asyncio.get_running_loop()
        container = HarborContainer(environment, loop)
        await asyncio.to_thread(self._run_sync, instruction, container, context)

    def _wait_for_odoo(self, container) -> bool:
        """Odoo loads its scenario after the container starts; acting before it lands
        gives the agent an empty database and a wasted trial."""
        deadline = time.time() + SETUP_TIMEOUT_S
        while time.time() < deadline:
            if container.exec(f"test -f {SETUP_MARKER}", timeout=30).ok:
                return True
            time.sleep(5)
        return False

    def _run_sync(self, instruction: str, container, context: AgentContext) -> None:
        started = time.time()
        trajectory_path = Path(self.logs_dir) / "trajectory.jsonl"
        trajectory = Trajectory(trajectory_path)
        terminal_reason = "crash"
        result = None
        kernel = None

        try:
            if not self._wait_for_odoo(container):
                raise RuntimeError(f"{SETUP_MARKER} did not appear within {SETUP_TIMEOUT_S}s")

            api_key = self._api_key()
            spec = self.models[self.model_key]
            llm = LLM(spec, api_key, logger=self.logger)

            tools = self.config.get("tools", ["python", "bash", "show", "finish"])
            kernel_needed = "python" in tools or "finish" in tools
            if kernel_needed:
                kernel = Kernel(container, lib_modules=self.config.get("lib") or None)
                kernel.start(env=CONTAINER_ENV)
                status = kernel.lib_status()
                self.lib_active = status.get("loaded", [])
                if status.get("failed"):
                    self.logger.warning("lib modules failed to load: %s", status["failed"])

            dispatch = self._make_dispatch(container, kernel)
            agent_loop = Loop(
                llm=llm,
                tools=schemas_for(tools),
                system_prompt=self._system_prompt(),
                instruction=instruction,
                run_tool=dispatch,
                trajectory=trajectory,
                step_cap=self.config.get("step_cap", 150),
                token_cap=self.config.get("token_cap", 1_500_000),
                ledger_k=self.config.get("ledger_k", 0),
                ledger_summary=self._ledger_summary(kernel) if kernel else None,
                briefing="",
                logger=self.logger,
            )
            result = agent_loop.run()
            terminal_reason = result.terminal_reason
        except Exception as exc:  # noqa: BLE001 - a crash must still produce a result
            self.logger.exception("agent crashed: %s", exc)
            trajectory.write(t=-1, role="system", content=f"crash: {type(exc).__name__}: {exc}",
                             tool=None, args=None, output=None, usage=None, latency_s=None)
        finally:
            if kernel:
                try:
                    kernel.stop()
                except Exception:  # noqa: BLE001
                    pass
            trajectory.close()

        usage = result.usage if result else None
        self.result = {
            "config": self.config_id,
            "model": self.models[self.model_key]["model"],
            "model_key": self.model_key,
            "terminal_reason": terminal_reason,
            "steps": result.steps if result else 0,
            "delegations": result.delegations if result else 0,
            "tokens": usage.as_dict() if usage else {"input": 0, "cached": 0, "output": 0},
            "cost_usd": round(usage.cost_usd, 6) if usage else 0.0,
            "wallclock_s": round(time.time() - started, 2),
            "lib_active": self.lib_active,
            "providers": result.providers if result else {},
            "summary": result.summary if result else "",
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        (Path(self.logs_dir) / "result.json").write_text(json.dumps(self.result, indent=2))

        if usage:
            context.n_input_tokens = usage.input
            context.n_cache_tokens = usage.cached
            context.n_output_tokens = usage.output
            context.cost_usd = usage.cost_usd
        context.metadata = {k: v for k, v in self.result.items() if k != "summary"}

    def _api_key(self) -> str:
        spec = self.models[self.model_key]
        key = self._get_env(spec["api_key_env"], "OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError(f"{spec['api_key_env']} is not set for the agent")
        return key

    # -- tools ----------------------------------------------------------------
    def _make_dispatch(self, container, kernel):
        bash_timeout = self.config.get("bash_timeout_s", 120)
        kernel_timeout = self.config.get("kernel_timeout_s", 120)
        gate = bool(self.config.get("finish_gate", False))

        def dispatch(name: str, args: dict) -> str:
            if name == "python":
                if kernel is None:
                    return "the python tool is not enabled for this configuration"
                reply = kernel.run(args.get("code", ""),
                                   timeout=int(args.get("timeout") or kernel_timeout))
                return self._render_kernel(reply)

            if name == "bash":
                result = container.exec(args.get("cmd", ""),
                                        timeout=int(args.get("timeout") or bash_timeout))
                body = result.stdout or ""
                if result.stderr.strip():
                    body += ("\n" if body else "") + f"[stderr]\n{result.stderr}"
                if result.rc != 0:
                    body += f"\n[exit {result.rc}]"
                return body or "(no output)"

            if name == "finish":
                summary = json.dumps(args.get("summary", ""))
                if kernel is None:
                    # No kernel means no checks to run: honour the finish immediately.
                    from harness.loop import FINISH_SENTINEL

                    return f"{FINISH_SENTINEL}\n(no check gate in this configuration)"
                code = (
                    "from lib.finish import finish as _finish\n"
                    f"print(_finish({summary}, gate={gate}))\n"
                )
                return self._render_kernel(kernel.run(code, timeout=kernel_timeout))

            return f"unknown tool {name!r}"

        return dispatch

    @staticmethod
    def _render_kernel(reply: dict) -> str:
        parts = []
        if reply.get("stdout"):
            parts.append(reply["stdout"].rstrip())
        if reply.get("stderr"):
            parts.append(reply["stderr"].rstrip())
        if not parts:
            parts.append("(no output)")
        return "\n".join(parts)

    def _ledger_summary(self, kernel):
        def summary() -> str:
            reply = kernel.run("print(plan.summary())", timeout=30)
            return (reply.get("stdout") or "").strip() or "(no plan set)"

        return summary
