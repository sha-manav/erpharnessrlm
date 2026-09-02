"""Reward-shape helpers, deliberately free of any Harbor import.

`harness/verifier.py` runs inside Harbor's virtualenv, but the analysis scripts and the
unit tests do not, and both need the same understanding of ERP-Bench's reward file. Keeping
the pure logic here lets everything share one definition.

ERP-Bench's `reward.json` (NOTES.md ## ERP-Bench / Reward semantics):

    {"overall_score": 0-100, "passed": bool,
     "constraint": {"earned": .., "total": ..}, "hygiene": {...}, "optimality": {...},
     "rules": {..., "by_dimension": {...}}, "spend": {...}, "optimality_detail": {...}}
"""

from __future__ import annotations

from typing import Any

# Nested blocks that hold lists of per-rule detail rather than scalars.
_SKIP_SUBKEYS = frozenset({"by_dimension", "rules_detail"})


def flatten_rewards(raw: Any, prefix: str = "") -> dict[str, float | int]:
    """Flatten nested reward blocks into scalar ``key_subkey`` entries.

    Non-numeric values (None, strings, lists) are dropped: Harbor's reward schema accepts
    only numbers, and the untouched `reward.json` on disk remains the source of truth for
    everything else.
    """
    flat: dict[str, float | int] = {}
    if not isinstance(raw, dict):
        return flat
    for key, value in raw.items():
        name = f"{prefix}{key}"
        if isinstance(value, bool):
            flat[name] = int(value)
        elif isinstance(value, (int, float)):
            flat[name] = value
        elif isinstance(value, dict) and key not in _SKIP_SUBKEYS:
            flat.update(flatten_rewards(value, prefix=f"{name}_"))
    return flat


def failed_rule_names(raw: dict, limit: int | None = None) -> list[str]:
    """The rules the verifier marked FAIL, as ``dimension: expression`` strings.

    Reads the verifier's *output* only — never a task's rule source — so it is safe to use
    on eval tasks (PLAN.md hard rule 1).
    """
    by_dimension = ((raw.get("rules") or {}).get("by_dimension")) or {}
    names: list[str] = []
    for dimension, rules in by_dimension.items():
        for rule in rules if isinstance(rules, list) else []:
            # `passed: False` is also set on rules that do not apply to this end state, so
            # matching on it alone over-reports: two dev40 tasks looked like they had
            # failing rules while the verifier counted `rules.failed == 0`. FAIL plus
            # applicable is the verifier's own definition.
            if rule.get("status") == "FAIL" and rule.get("applicable") is not False:
                names.append(f"{dimension}: {rule.get('expr') or rule.get('rule')}")
    return names[:limit] if limit else names
