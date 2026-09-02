"""The agent loop: messages, tools, caps, paging, ledger, logging (PLAN.md P2.7).

Design decisions here are answers to measurements taken on config A (NOTES.md ## Reproduction):

* **Append-only messages with a static prefix.** 1.25M input tokens per trial, 80% of them
  cache reads. Rewriting earlier messages would break the prefix and turn cache reads back
  into full-price input, so the transcript is only ever appended to.
* **Every tool result is paged.** pi's bash tool admits 50 KB per call into a context that
  is re-sent every turn; here anything over 4k characters becomes a handle plus a 3k first
  page, and the model pays for more only if it asks.
* **A step cap and a token cap.** pi has neither. 72% of config A's spend went to trials
  that failed, which ran 34 steps against 22 for passes; the caps bound that tail.
* **A ledger reminder every K steps.** Constraints stated in the instruction stop being
  mentioned around step 10 and then go unsatisfied.
* **Loop detection.** Three identical calls in a row is not persistence, it is a stuck
  agent paying full price per attempt.

Terminal reasons: `finish`, `step_cap`, `token_cap`, `api_error`, `loop`, `crash`.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from harness.llm import LLM, LLMError, Usage
from harness.lib.fmt import PageStore

FINISH_SENTINEL = "__ERP_HARNESS_" "FINISHED__"   # split so a printed source never contains it
REPEAT_LIMIT = 3


@dataclass
class LoopResult:
    terminal_reason: str
    steps: int
    usage: Usage
    delegations: int = 0
    summary: str = ""
    providers: dict[str, int] = field(default_factory=dict)


def _normalise_tool_calls(message: dict) -> dict:
    """Make an assistant message safe to send back.

    `tool_calls[].function.arguments` must be a JSON *string* on the way back in. Providers
    are not consistent about what they emit — an object, or an empty string when the model
    called a tool with no arguments — and echoing either verbatim gets the whole request
    rejected: `HTTP 400 Invalid tool_calls.function.arguments value, expected JSON`. That
    kills the trajectory at whatever step it happens, so it is repaired here rather than
    left to the provider.
    """
    calls = message.get("tool_calls")
    if not calls:
        return message
    repaired = []
    for call in calls:
        call = dict(call)
        function = dict(call.get("function") or {})
        arguments = function.get("arguments")
        if isinstance(arguments, (dict, list)):
            function["arguments"] = json.dumps(arguments)
        elif not isinstance(arguments, str) or not arguments.strip():
            function["arguments"] = "{}"
        else:
            try:
                json.loads(arguments)
            except ValueError:
                # A call cut off by max_tokens leaves a half-written JSON string. Echoing
                # it back is rejected outright ("Invalid tool_calls.function.arguments
                # value, expected JSON"), which ends the trajectory over one bad turn.
                function["arguments"] = "{}"
                function["_truncated"] = True
        call["function"] = function
        repaired.append(call)
    message["tool_calls"] = repaired
    return message


class Trajectory:
    """Append-only JSONL log in the common schema."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("w")

    def write(self, **event) -> None:
        event.setdefault("ts", datetime.now(timezone.utc).isoformat())
        event.setdefault("agent_id", "root")
        self._handle.write(json.dumps(event, default=str) + "\n")
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()


class Loop:
    def __init__(
        self,
        llm: LLM,
        tools: list[dict],
        system_prompt: str,
        instruction: str,
        run_tool: Callable[[str, dict], str],
        trajectory: Trajectory,
        step_cap: int = 150,
        token_cap: int = 1_500_000,
        ledger_k: int = 0,
        ledger_summary: Callable[[], str] | None = None,
        briefing: str = "",
        logger=None,
        time_budget_s: float = 0,
    ):
        self.llm = llm
        self.tools = tools
        self.run_tool = run_tool
        self.trajectory = trajectory
        self.step_cap = step_cap
        self.token_cap = token_cap
        self.ledger_k = ledger_k
        self.ledger_summary = ledger_summary
        self.logger = logger
        self.pages = PageStore()
        # Wall-clock awareness. The benchmark kills an episode at the task's agent
        # timeout (3600 s on every task); the stock harness never hits it (13-step
        # episodes) while this one rehearses and reasons at length, and a dev40 trial was
        # cut off at step 40 with a five-plan comparison done and nothing executed. The
        # model is told the budget up front, sees elapsed time in every status line, and
        # is told once at 70% to execute now and once at 88% to finish now.
        self.time_budget_s = float(time_budget_s or 0)
        self.started = time.time()
        self._time_warned: set[str] = set()

        first_user = instruction
        if briefing:
            first_user = f"{instruction}\n\n## Environment at task start\n\n{briefing}"
        first_user += f"\n\nStep cap: {step_cap}."
        if self.time_budget_s:
            first_user += (f" Time budget: {self.time_budget_s / 60:.0f} minutes of wall-clock — "
                           "the episode is cut off after it, so a plan that is not executed on "
                           "the main database by then scores nothing.")

        # Plain content: breakpoints are placed per request by llm.apply_cache_control,
        # which must move to the end of the transcript each turn to cache the growing prefix.
        self.messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": first_user},
        ]
        self.trajectory.write(t=0, role="system", content=system_prompt, tool=None,
                              args=None, output=None, usage=None, latency_s=None)
        self.trajectory.write(t=0, role="user", content=first_user, tool=None,
                              args=None, output=None, usage=None, latency_s=None)

    # -- helpers --------------------------------------------------------------
    def _append_user(self, text: str, kind: str) -> None:
        self.messages.append({"role": "user", "content": text})
        self.trajectory.write(t=self.step, role="user", content=text, tool=None,
                              args=None, output=None, usage=None, latency_s=None,
                              kind=kind)

    def _signature(self, call: dict) -> tuple:
        function = call.get("function") or {}
        return (function.get("name"), function.get("arguments"))

    def _elapsed(self) -> float:
        return time.time() - self.started

    def _time_check(self) -> None:
        """Two warnings, each once: at 70% of the budget, and at 88%."""
        if not self.time_budget_s:
            return
        frac = self._elapsed() / self.time_budget_s
        left = max(0.0, self.time_budget_s - self._elapsed()) / 60
        if frac >= 0.88 and "final" not in self._time_warned:
            self._time_warned.update({"final", "warn"})
            self._append_user(
                f"[time] about {left:.0f} minutes remain before the episode is cut off. "
                "Do not start anything new: make sure what is on the main database is "
                "confirmed and consistent, then call finish with a summary.", "time_final")
        elif frac >= 0.70 and "warn" not in self._time_warned:
            self._time_warned.add("warn")
            self._append_user(
                f"[time] {self._elapsed() / 60:.0f} of {self.time_budget_s / 60:.0f} minutes used; "
                f"about {left:.0f} remain. Stop comparing alternatives. Execute the best "
                "feasible plan you have on the main database now (rehearse once at most), "
                "then call finish.", "time_warning")

    # -- main loop ------------------------------------------------------------
    def run(self) -> LoopResult:
        self.step = 0
        self.started = time.time()
        recent: list[tuple] = []
        summary = ""

        while True:
            if self.step >= self.step_cap:
                return self._done("step_cap", summary)
            if self.llm.total.input >= self.token_cap:
                return self._done("token_cap", summary)
            self._time_check()

            self.step += 1
            try:
                reply = self.llm.complete(self.messages, self.tools)
            except LLMError as exc:
                if self.logger:
                    self.logger.error("llm failed at step %d: %s", self.step, exc)
                self.trajectory.write(t=self.step, role="system", content=f"api_error: {exc}",
                                      tool=None, args=None, output=None, usage=None,
                                      latency_s=None)
                return self._done("api_error", summary)

            assistant = _normalise_tool_calls(dict(reply.message))
            assistant.setdefault("role", "assistant")
            self.messages.append(assistant)
            self.trajectory.write(
                t=self.step, role="assistant", content=reply.text,
                tool=None, args=None, output=None,
                usage=reply.usage.as_dict(), latency_s=reply.latency_s,
                cost_usd=reply.usage.cost_usd, provider=reply.provider,
                finish_reason=reply.finish_reason,
            )

            calls = assistant.get("tool_calls") or []
            if reply.finish_reason == "length":
                self._append_user(
                    "Your previous message hit the output limit and was cut off mid-call. "
                    "Send a shorter tool call — build the work up over several calls "
                    "rather than one long one.", "truncated")
            if not calls:
                # A model that stops calling tools without finishing has stopped working;
                # say so once and give it another turn rather than ending the episode.
                self._append_user(
                    "You did not call a tool. If the task is complete, call finish with a "
                    "summary. Otherwise continue.", "nudge")
                continue

            for call in calls:
                signature = self._signature(call)
                recent.append(signature)
                recent[:] = recent[-REPEAT_LIMIT:]
                repeated = len(recent) == REPEAT_LIMIT and len(set(recent)) == 1

                name = (call.get("function") or {}).get("name", "")
                try:
                    args = json.loads((call.get("function") or {}).get("arguments") or "{}")
                except (ValueError, TypeError):
                    args = {}

                started = time.time()
                if name == "show":
                    # Served here rather than by the dispatcher: the page store that minted
                    # the handle lives in the loop, because the loop is what truncated the
                    # output in the first place.
                    output = self.pages.show(
                        str(args.get("handle", "")), int(args.get("page") or 1))
                else:
                    try:
                        output = self.run_tool(name, args)
                    except Exception as exc:  # noqa: BLE001 - a tool failure is data
                        output = f"tool {name} raised {type(exc).__name__}: {exc}"
                latency = round(time.time() - started, 2)

                shown = self.pages.capture(output or "(no output)")
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": shown,
                })
                self.trajectory.write(
                    t=self.step, role="tool", content=None, tool=name, args=args,
                    output=shown, usage=None, latency_s=latency,
                )

                # Only the finish tool ends the episode (or a python call whose output the
                # agent verified as a real finish). A pass-3 trial ended as "finish" with
                # nothing executed because it `cat`ed the finish module over bash and the
                # sentinel literal came back in the output.
                if name in ("finish", "python") and FINISH_SENTINEL in (output or ""):
                    summary = args.get("summary", "")
                    return self._done("finish", summary)

                if repeated:
                    self._append_user(
                        f"[warning] you have called {name} with identical arguments "
                        f"{REPEAT_LIMIT} times in a row. Change approach.", "loop_warning")
                    if self.logger:
                        self.logger.warning("loop detected at step %d on %s", self.step, name)
                    return self._done("loop", summary)

            if self.ledger_k and self.step % self.ledger_k == 0 and self.ledger_summary:
                try:
                    ledger = self.ledger_summary()
                except Exception as exc:  # noqa: BLE001
                    ledger = f"(ledger unavailable: {exc})"
                clock = (f" · {self._elapsed() / 60:.0f}/{self.time_budget_s / 60:.0f} min"
                         if self.time_budget_s else "")
                self._append_user(
                    f"[status] step {self.step}/{self.step_cap}{clock} · {ledger}", "ledger")

    def _done(self, reason: str, summary: str) -> LoopResult:
        if self.logger:
            self.logger.info(
                "loop finished: %s after %d steps, %d input tokens (%d cached), $%.3f",
                reason, self.step, self.llm.total.input, self.llm.total.cached,
                self.llm.total.cost_usd)
        return LoopResult(
            terminal_reason=reason,
            steps=self.step,
            usage=self.llm.total,
            summary=summary,
            providers=dict(self.llm.providers),
        )
