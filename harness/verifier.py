"""A Harbor verifier that tolerates ERP-Bench's nested ``reward.json``.

Why this exists (NOTES.md ## ERP-Bench / Reward semantics):

ERP-Bench's ``tests/test.sh`` writes a *nested* ``/logs/verifier/reward.json``
(``{"overall_score": ..., "constraint": {"earned": .., "total": ..}, ...}``), which
Harbor 0.22.0 rejects because ``VerifierResult.rewards`` is typed ``dict[str, float|int]``.
The benchmark was authored against an older Harbor whose reward schema was looser.

This subclass changes nothing about *how the task is scored* — the same ``test.sh``
runs, the same ``reward.json`` lands on disk untouched. It only flattens the parsed
dict (``constraint.earned`` -> ``constraint_earned``) so Harbor's own bookkeeping
validates. Analysis reads the raw ``reward.json`` for per-rule detail.

Used via: ``harbor run ... --verifier harness.verifier:FlatVerifier``
"""

from __future__ import annotations

import json
from typing import Any

from harbor.verifier.verifier import (
    Verifier,
    RewardFileEmptyError,
    VerifierOutputParseError,
)

# The flattening itself lives in a Harbor-free module so the analysis scripts and the
# unit tests (which run outside Harbor's virtualenv) share exactly one definition.
from harness.rewards import flatten_rewards


class FlatVerifier(Verifier):
    def _parse_reward_json(self) -> dict[str, float | int]:
        path = self.trial_paths.reward_json_path
        if path.stat().st_size == 0:
            raise RewardFileEmptyError(f"Reward file is empty at {path}")
        try:
            raw = json.loads(path.read_text())
        except (ValueError, TypeError) as exc:
            raise VerifierOutputParseError(
                f"Failed to parse rewards from JSON file {path}"
            ) from exc

        flat = flatten_rewards(raw)
        if not flat:
            raise VerifierOutputParseError(
                f"No numeric rewards found in {path} (keys: {sorted(raw) if isinstance(raw, dict) else type(raw)})"
            )
        # Harbor's headline number: keep `reward` pointing at the benchmark's own
        # overall score so the CLI's mean matches what we report.
        if "overall_score" in flat:
            flat.setdefault("reward", flat["overall_score"])
        return flat
