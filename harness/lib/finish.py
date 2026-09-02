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

FINISH_SENTINEL = "__ERP_HARNESS_" "FINISHED__"   # split so a printed source never contains it
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

    Used to label, not to exempt: a repair-plan task's whole job is to fix what was
    already broken. The label explains the failure's origin so the model can decide
    whether to repair it or justify it in the summary.
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


def _main_write_count() -> int:
    try:
        from .erp import erp
        return len(erp.write_log)
    except Exception:       # noqa: BLE001 - no erp module in this configuration
        return -1


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
        # A failure that already existed at task start is labelled, but it still blocks.
        # Excluding it was wrong: 28 of the 300 tasks are repair plans, where fixing what
        # was already broken IS the task, and the seeded patterns ship draft orders that
        # are meant to be confirmed. The label tells the model why the check fails and
        # that the summary must say so if it genuinely cannot be repaired; after three
        # refusals the episode ends regardless.
        for row in hard_fails:
            if row["check"] in _state["baseline"]:
                row["evidence"] = "(already failing at task start) " + row["evidence"]
        failing = hard_fails

    # A finish with nothing written to the main database is almost never the end of an
    # ERP task; pass 3 produced one (a clean rehearsal, then the episode wandered off and
    # ended with no orders on main — every check vacuously green). Refused once: if the
    # task truly requires no change, the summary says so and the second call goes through.
    if gate and not _state.get("empty_refused") and _main_write_count() == 0:
        _state["empty_refused"] = True
        _state["refusals"] += 1
        return (
            f"finish refused (attempt {_state['refusals']}/{MAX_REFUSALS}): no changes have "
            "been written to the main database (erp.write_log is empty). A rehearsal on a "
            "clone does not count — run the plan against `erp` itself. If this task genuinely "
            "requires no change, say so in the summary and call finish again."
        )

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


# `finish.reset()` / `finish.record_baseline()` are what a reader of the module docs
# reaches for; they cost a pass-3 trial its ending. Both spellings work.
finish.reset = reset                    # type: ignore[attr-defined]
finish.record_baseline = record_baseline  # type: ignore[attr-defined]
finish.set_baseline = set_baseline      # type: ignore[attr-defined]
