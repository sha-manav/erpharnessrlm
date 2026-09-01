"""The library preloaded into every kernel namespace, inside the task container.

Which modules are present is decided per config (`configs/configs.yaml: lib`), and the
agent copies only those in. So `__all__` is computed from what is actually on disk rather
than hard-coded: a config that ships `[erp, plan]` gets exactly those two, and
`result.json.lib_active` reports what loaded.

Each module may declare `EXPORTS = ["erp", "OdooError", ...]` — those names are bound
directly in the namespace so the agent can write `erp.stock()` rather than
`lib.erp.erp.stock()`.
"""

from __future__ import annotations

from pathlib import Path

# Load order matters: later modules import earlier ones (check needs erp, state needs db).
_PREFERRED_ORDER = ["fmt", "erp", "db", "state", "plan", "check", "finish", "delegate"]


def _discover() -> list[str]:
    present = {
        path.stem
        for path in Path(__file__).parent.glob("*.py")
        if path.stem != "__init__"
    }
    ordered = [name for name in _PREFERRED_ORDER if name in present]
    ordered += sorted(present - set(ordered))
    return ordered


__all__ = _discover()
