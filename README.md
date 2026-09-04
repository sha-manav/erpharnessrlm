# ERP-Harness

A domain-specific agent harness for **ERP-Bench** (300 verifiable Odoo 19 procurement /
manufacturing tasks in Harbor format), evaluated against the generic `pi` coding-agent
harness, with ablations and token/cost measurement.

See `PLAN.md` for the full research plan, `PROGRESS.md` for task status, and `NOTES.md`
for everything discovered about Harbor, ERP-Bench, pi, Odoo and the model endpoints.

No fine-tuning: all gains come from the harness.

## Result (frozen at tag `harness-v1-eval100`)

Same model (GLM-5.1, fp8, same provider pin), 100 held-out tasks, no training:

| eval100 | pi (stock coding agent) | this harness |
|---|---|---|
| pass@1 | 39/100 | **72/100** |
| mean reward | 54.7 | **88.9** |
| paired difference | | +33 pts, 95% CI [+22, +43], McNemar p = 1e-7 |
| $/task | 0.62 | 0.83 |

Ablation on a 30-task eval slice: loop + finish tool without the domain library 10/30,
pi 15/30, this harness 23/30 — the library is the gain, not the loop.

- `analysis/writeup.md` — the account: setup, what the harness is, how failures drove
  each primitive, results, ablation, failure taxonomy, threats to validity.
- `analysis/fig1_pareto.png`, `fig2_harness_groups.png`, `fig_tokens.png`.
- `analysis/freeze.md` — the exact code, routing, caps and batch provenance of the eval run.
- `analysis/eval100_C_full.json`, `eval100_A_pi.json`, `eval30_B_bash.json` — per-task tables;
  `scripts/stats.py`, `scripts/figures.py`, `scripts/merge_batches.py` reproduce the numbers.
