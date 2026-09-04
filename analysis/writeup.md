# A domain harness for ERP-Bench: 39% → 72% with the same model and no training

*Repository: sha-manav/erpharnessrlm. Frozen version: tag `harness-v1-eval100`
(`analysis/freeze.md`). All numbers below are reproducible from `analysis/*.json`,
`analysis/results.csv` and the scripts in `scripts/`.*

## 1. Summary

ERP-Bench is 300 verifiable procurement and manufacturing tasks in a live Odoo 19
database (29 task patterns; each grader scores constraints, hygiene and optimality on
the final database state). The public leaderboard runs a generic coding-agent harness
(`pi`). We built a domain-specific harness around the same model — GLM-5.1 at fp8 via
OpenRouter, same provider pin, same token accounting — and compared the two on a
frozen, held-out set of 100 tasks that the harness never saw during development.

| eval100 (100 held-out tasks) | pi (stock) | this harness (C_full v1) |
|---|---|---|
| pass@1 | 39/100 | **72/100** |
| mean reward (0–100) | 54.7 | **88.9** |
| paired difference (pass@1) | | **+33 pts, 95% CI [+22, +43], McNemar p = 1e-7** |
| wins / losses, paired by task | | 37 tasks won that pi lost / 4 lost that pi won |
| cost per task (USD) | 0.62 | 0.83 |
| cache hit rate | 75% | 86% |
| output tokens per task | 37k | 96k |

By difficulty: easy 17/18 vs 12/18, medium 45/57 vs 22/57, hard 10/25 vs 5/25.

The lift is the harness, not the model: the model, sampling settings and provider are
identical. It is also not the loop alone: on the 40-task development set, bash plus a
finish tool scores 17.5% on dev40 and 33% on a 30-task eval slice where pi scores 50% and
this harness 77%; the gain arrives with the domain library (figure 2, §5). This is the shape of result Harvey reported for legal diligence with an
RLM-style harness: a REPL with domain primitives and a gated finish, no fine-tuning.

## 2. Setup

**Benchmark.** ERP-Bench, Harbor format, one Odoo 19 + Postgres container per task. The
verifier reads the final database and scores per-rule flags plus an optimality term;
`passed` requires essentially every dimension to be met. `configs/eval100.txt` (100
tasks) was frozen before any harness code existed; `configs/dev40.txt` (40 disjoint
tasks, all 29 patterns) drove development. A commit hook (`scripts/guard.sh`) refuses
any read of an eval task's tests, solution or grader; dev-task graders were read
during iteration, as the plan allowed.

**Model.** `z-ai/glm-5.1`, fp8 only, routed GMICloud → Baidu → StreamLake in order
(fp4 upstreams excluded after a contaminated run). No temperature is sent, matching
pi. Cost, cached tokens and serving provider are recorded per request from
OpenRouter's usage block.

**Configurations.**

- **A_pi** — pi 0.84.4 unchanged (bash, read, write, edit tools).
- **B_bash** — our loop with bash and a finish tool, no library: isolates the loop.
- **C_full** — our loop with a persistent Python kernel inside the task container, a
  preloaded domain library, a briefing, a plan ledger, database snapshots for
  rehearsal, and a finish tool gated on domain invariants. v0 is the first complete
  build; v1 is after the dev-set iteration described in §4.

**Caps** (same across our configs): 150 steps, 3M input tokens, 600 s kernel calls,
a 3,300 s wall-clock budget inside the tasks' 3,600 s agent timeout.

## 3. What the harness is

The model works in a Python REPL, not a shell. The kernel is preloaded with:

- `erp` — a typed Odoo client whose write helpers refuse infeasible writes at write
  time: a purchase dated before order date + the vendor's lead time, a receipt for
  goods that have not had time to arrive, an MO starting before its components can
  exist or with a due date before start + lead time, an origin naming an order the
  document cannot feed (by date or by quantity), a second PO on a supplier offer.
  Planning helpers make the arithmetic a function call: `feasible_vendors`,
  `cheapest_buy` (exact min-cost split across vendor tiers, minimums and note-stated
  maxima), `earliest_build` (make-or-buy per component, lead times, work-centre
  options), `workcenter_options` (minutes per unit, minutes free, units that fit).
- `check` — 16 invariants from ERP practice, run before finish and on rehearsal
  clones: no drafts, non-negative stock, supplier validity, invoicing, demand covered,
  receipts before the demand they feed, components on hand when an MO starts (a
  start-order simulation), nothing received before it could arrive, origin dates and
  origin quantities (a max-flow from supplies to the orders they name), MO
  schedulability, work-centre capacity, one PO per offer, and a soft reminder for
  uninvoiced retained orders. Every check names the failing record and the fix.
- `state` — Postgres snapshots (`CREATE DATABASE … TEMPLATE`) reachable over RPC, so
  `state.rehearse(plan_fn)` runs a plan on a throwaway clone and returns the check
  table; a plan that writes to the real database during a rehearsal is flagged.
- `plan` — a ledger with the task's stated objective, re-injected every 15 steps;
  `brief()` — a front-loaded summary of demand, stock, offers, BOMs and work centres.
- `finish` — refuses (up to three times) while a hard check fails or nothing has been
  written to the main database; only the finish tool can end the episode.

Prompt-side, a four-step contract (write the rules down, plan, rehearse, execute once,
finish) and a playbook of Odoo 19 traps found empirically (`button_mark_done` consumes
nothing unless raw moves are written; onchange defaults do not fire over RPC; etc.).

## 4. How it was built: measure, then build what the failures ask for

The first measurement (`analysis/failures_stock_big.md`) coded pi's 23 dev-set
failures: 17 were plans right in vendor, quantity and price and wrong in dates —
receipts landing after the demand they feed. That set the build order
(`analysis/build_order.md`): timeline invariants first, rehearsal second, briefing
third, delegation last ("expect little"; not built).

C_full v0 then *lost* to pi on dev40 (22.5% vs 42.5%). Reading its trajectories gave
four defects — a cache breakpoint that never moved, a finish gate that refused
nothing, library modules the prompt promised but the config did not ship, and a
transport path that dropped the finish call — and five dev-set checkpoint passes
found the rest, each from a specific trajectory:

| found in | defect | fix |
|---|---|---|
| pass 1 | timeline check trusted the written `date_planned` | arrival = max(date_planned, order + lead), received lines not exempt |
| pass 2 | model used `receive(force=True)` and the checks said nothing | `no_fabricated_receipts` (hard) |
| pass 3 | PO origin named orders due before the goods land | `origin_consistent` + write-time guard |
| pass 3 | MOs had no due date / work centre; component POs mis-timed (pi fails 7 of 8 make tasks) | `create_mo` writes deadline/origin/centre; `mo_schedule`, `workcenter_capacity`, start-order `mo_feasible` |
| pass 3 | episode ended by a sentinel in a `cat` of source; finish with nothing written | only the finish tool ends an episode; refuse an empty finish |
| make7 | spend a hair above the reference with every rule green | `cheapest_buy`; stated objective carried in the plan |
| dev40 v1 | invoicing policy applied only to delivered orders; two POs on one offer; alternate work-centre rates not in Odoo; hour-limit timeouts; origin quantities | policy on every retained order; `po_consolidated`; 1.5× margin on alternates; wall-clock budget; `origin_flow` |

Result on dev40: v0 22.5% → v1 60%, against pi's 42.5% (figure 2). Every fix is a
domain rule or an Odoo fact; nothing under `harness/` refers to a task pattern.

## 5. Results

**Eval100** (table in §1; `analysis/eval100_C_full.json`, `analysis/eval100_A_pi.json`;
`scripts/stats.py`). The paired bootstrap interval on pass@1 is +22 to +43 points; on
mean reward +26 to +43. Of the 100 tasks, 35 are passed by both, 24 by neither, 37 by
this harness only, 4 by pi only.

**Cost** (figure 1). Per task the harness costs 33% more than pi: it reasons 2.6× as
much (96k vs 37k output tokens) and reads more (1.2M vs 1.0M input), but an 86% cache
hit rate keeps the input side cheap. Steps are similar (33.5 vs 27.7).

**Ablation** (figure 2). On dev40: A_pi 42.5%, B_bash 17.5%, C_full v0 22.5%, C_full v1
60%. On a 30-task stratified slice of eval100 (`configs/eval30.txt`, chosen by position
in sorted order per difficulty, no outcomes consulted), all three paired on the same
tasks:

| eval30 slice | B_bash (loop + finish, no library) | pi | C_full v1 |
|---|---|---|---|
| pass@1 | 10/30 (33.3%) | 15/30 (50.0%) | 23/30 (76.7%) |
| mean reward | 52.8 | 58.2 | 88.0 |
| $/task | 0.84 | 0.66 | 0.83 |

C_full vs B_bash: +43 points, 95% CI [+23, +63], discordant 14 vs 1, McNemar p = 1e-3.
pi vs B_bash: +17 points, CI [−3, +37], p = 0.18. The loop with a finish tool and no
domain library is no better than pi at the same cost; the library is what moves the
number. (`analysis/eval30_B_bash.json`; `scripts/stats.py … --subset configs/eval30.txt`.)

**Failures** (`analysis/failures_eval100.md`). Of the harness's 28 misses, 12 have every
constraint and hygiene rule green and lose on the optimality term alone (nine score
above 90); 5 hit the 3M-token cap and 2 the hour limit, all on hard tasks; 4 are origin
traceability, 3 capacity, 2 timing — the families the library models, residual on
multi-level sub-assembly plans.

## 6. Threats to validity

- **Tuned to this benchmark's conventions.** The rules were calibrated on the 40 dev
  tasks and their graders. Most are ERP practice stated in the task instructions
  (origin format, invoicing policy, one PO per offer, minute limits in work-centre
  notes). Two are hedges against numbers the grader knows and Odoo does not: the 1.5×
  rate assumed for alternate work centres, and the exact "minimum-quantity excess only
  on components" clause of origin traceability. A differently written benchmark would
  need those re-derived.
- **Same distribution.** "Held out" means unseen instances of the same 29 patterns,
  not a different ERP or task family.
- **Ablation coverage.** B_bash and the C_minus variants ran on dev40; on eval only
  the B_bash slice. The library-vs-loop attribution rests on dev40 plus that slice.
- **Provenance of the eval run.** One library rule (the finished-goods origin clause)
  was patched after 19 of the 100 trials had started; the run was stitched from five
  batches (a concurrency restart, two network drops, a credit floor) and merged by
  first completed result per task; five tasks were rerun after dying to a network
  drop, and one of them (2297) scored 99.9 on the killed attempt and 20.4 on the
  rerun that stands. All recorded in `NOTES.md` and `analysis/freeze.md`.
- **Single run, live provider.** One pass per task per config; the same model served
  by different upstreams across the day (GMICloud for dev40, Baidu for most of eval).
- **Reward hacking.** The harness cannot touch the verifier; its checks only refuse
  actions or refuse finish; the one shortcut the environment permits (receiving goods
  that have not arrived) is blocked, which initially lowered the score.

## 7. What is left

The optimality tail (12 all-green misses) and the token-cap hits on hard tasks are the
two remaining families a next version would target; delegation (P3.4) was ranked last
by the evidence and not built; the remaining ablations need roughly $70 each on eval.
