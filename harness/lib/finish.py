"""The finish tool, and the gate in front of it (PLAN.md P2.4).

Harvey's harness result attributes much of its gain to a finish tool that refuses while
work is undone. The measured case for it here: config A ends every trial with
`terminal_reason: finish` — the model stops when it believes it is done, and 27 of 49 of
those beliefs were wrong. Nothing ever told it otherwise.

`finish(summary)` runs `check.all()` first. While a **hard** check fails, it refuses and
returns the failing rows, and the episode continues. After three refusals it lets go: a
model that cannot satisfy the checks should spend its remaining steps on the task rather
than in a loop against the gate, and a harness that can never terminate is worse than one
that terminates imperfectly.

The loop detects completion by the `FINISHED` sentinel in stdout, so `finish` works
identically whether the model calls the tool or calls the function from inside `python`.
"""

from __future__ import annotations

import os
from pathlib import Path

EXPORTS = ["finish", "FINISH_SENTINEL"]

FINISH_SENTINEL = "__ERP_HARNESS_FINISHED__"
MAX_REFUSALS = 3
SUMMARY_PATH = os.environ.get("ERP_SUMMARY_PATH", "/output/summary.md")

_state = {"refusals": 0, "finished": False, "baseline": set()}


def reset() -> None:
    """Start a fresh episode (used by tests and by the agent at task start)."""
    _state["refusals"] = 0
    _state["finished"] = False
    _state["baseline"] = set()


def set_baseline(failing_ids) -> list[str]:
    """Record checks that were already failing before the agent acted.

    Seeded scenarios ship with pre-existing orders, and some of those violate an
    invariant on arrival. Refusing `finish` over a condition the agent did not cause and
    cannot be expected to repair burns its attempts on nothing, so those are reported but
    never block. Everything the agent touches is still held to every check.
    """
    _state["baseline"] = set(failing_ids)
    return sorted(_state["baseline"])


def record_baseline(client=None) -> list[str]:
    """Run the invariants now and remember which already fail. Called at task start."""
    try:
        from . import check as check_module
    except ImportError:
        return set_baseline([])
    table = check_module.invariants(client)
    return set_baseline(r["check"] for r in table.all() if r["status"] == "FAIL")


def refusals() -> int:
    return _state["refusals"]


def _write_summary(summary: str, checks_text: str) -> str:
    path = Path(SUMMARY_PATH)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{summary.strip()}\n\n## Checks at finish\n\n```\n{checks_text}\n```\n")
        return str(path)
    except OSError as exc:
        # Never lose a finished episode over a missing directory.
        return f"(could not write {path}: {exc})"


def finish(summary: str = "", client=None, gate: bool = True) -> str:
    """End the episode, if the hard checks agree.

    Returns either the refusal text (episode continues) or a string containing
    `FINISH_SENTINEL`, which the loop treats as terminal.
    """
    # `check` is ablatable (C_minus_dry ships without it), so it is imported here rather
    # than at module load: no checks simply means no gate, not a broken finish tool.
    try:
        from . import check as check_module
    except ImportError:
        check_module = None

    if check_module is None:
        checks_text = "(no checks in this configuration)"
        failing, preexisting = [], []
    else:
        table = check_module.all(client)
        checks_text = str(table)
        hard_fails = [r for r in table.all() if r["status"] == "FAIL" and r["hard"] == "hard"]
        preexisting = [r for r in hard_fails if r["check"] in _state["baseline"]]
        failing = [r for r in hard_fails if r["check"] not in _state["baseline"]]
        if preexisting:
            checks_text += "\n(pre-existing at task start, not blocking: " + \
                ", ".join(r["check"] for r in preexisting) + ")"

    if gate and failing and _state["refusals"] < MAX_REFUSALS - 1:
        _state["refusals"] += 1
        lines = "\n".join(f"  {row['check']}: {row['evidence']}" for row in failing)
        return (
            f"finish refused (attempt {_state['refusals']}/{MAX_REFUSALS}): "
            f"fix or downgrade.\n{len(failing)} hard check(s) failing:\n{lines}\n\n"
            "Fix them and call finish again, or explain in the summary why a check does "
            "not apply to this task."
        )

    _state["finished"] = True
    written = _write_summary(summary or "(no summary given)", checks_text)
    note = ""
    if failing:
        note = (
            f"\nProceeding with {len(failing)} hard check(s) still failing after "
            f"{_state['refusals']} refusal(s):\n"
            + "\n".join(f"  {row['check']}: {row['evidence']}" for row in failing)
        )
    return f"{FINISH_SENTINEL}\nsummary written to {written}{note}"
