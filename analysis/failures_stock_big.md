# Failure analysis — stock harness (config A, GLM-5.1, dev40)

Run `A_pi__big__dev40__20260902T002458Z`, commit `9c3e0b1`. 40 dev tasks, one trial each,
all 40 terminating cleanly (`finish:40` — no crashes, no timeouts, no step caps).

| | |
|---|---|
| pass@1 | **42.5%** (17/40) |
| mean reward | 55.3 |
| mean steps | 29.2 |
| mean input tokens | 1,197,008 |
| mean cost | $0.656 |
| failures coded | **23** |

Every code below comes from the verifier's *output* (`reward.json`'s `rules.by_dimension`
flags) plus our own trajectory. No task's `tests/` or `solution/` was read.

## Counts

| code | n | share |
|---|---:|---:|
| **TIMELINE** | **17** | **74%** |
| PREMATURE_FINISH | 2 | 9% |
| SUBOPTIMAL | 2 | 9% |
| MISSING_DOC | 1 | 4% |
| OVERSPEND | 1 | 4% |
| SUPPLIER, DRAFT, BOM_MATH, API_ERR, TOOL_LOOP, STEP_CAP, SCOPE, OTHER | 0 | — |

Underlying rule families, counted by tasks affected:

```
18  supply_timing_feasible               5  component_stock_capacity_compliance
13  mo_schedule_compliance               5  po_origin_traceability
11  po_delivery_schedule_compliance      2  seeded_order_confirmed / supply_coverage
11  mo_component_feasibility             2  budget_compliance / sale_revenue
```

## TIMELINE — 17 of 23

`2017`, `2065`, `2070`, `2086`, `2097`, `2109`, `2122`, `2130`, `2147`, `2156`, `2167`,
`2190`, `2204`, `2217`, `2235`, `2249`, `2258`

The dominant failure by a wide margin, and it is not an execution failure: these
trajectories create the right orders for the right quantities from listed vendors, and
then the dates do not work. `supply_timing_feasible` fails when a receipt lands after the
customer due date it is meant to cover (`order date + supplierinfo.delay`), and
`mo_component_feasibility` / `mo_schedule_compliance` fail when a manufacturing order is
scheduled before the components it consumes can arrive.

The mechanism is visible in `2109` (reward 20.0, 22 steps). Steps 2–6 are five separate
throwaway `gather_info*.py` scripts, each re-opening its own `odoolib` connection and
printing raw records; the model then commits to a plan at step 13 (`execute_plan.py`) with
`date_planned` computed as `datetime.now() + timedelta(...)` — a date chosen for
convenience, never checked against the vendor's `delay` or the order's `commitment_date`.
Steps 16–20 are repair attempts (`fix_create_pos.py`, `confirm_all.py`,
`final_verify.py`) that fix quantities and confirmations but never revisit the dates,
because nothing tells the model the dates are the problem.

Notably these tasks are *nearly* right: constraint scores cluster at 43–158 of 55–170
earned, and reward at 19–23. The plans are one arithmetic relation away from passing.

## PREMATURE_FINISH — 2 of 23

`2050` (reward 0.0, 17 steps), `2233` (reward 3.9, 11/71 constraints)

Both stopped with the substantive work undone. `2050` fails `has_relevant_activity` — after
17 steps it had confirmed nothing at all, and its spend is $0.00 against an expected
$0.00 only because it never bought anything. `2233` fails `demand_coverage` and
`deadline_fulfillment` outright. In both, the model wrote a plan, hit friction, and
declared completion; nothing in the stock harness disagrees with a model that says it is
finished.

## SUBOPTIMAL — 2 of 23

`2221` (reward 40.2, optimality 0.32), `2244` (reward 67.7, optimality 46.2)

**Zero failed rules in either.** The end states are valid — every constraint and hygiene
check satisfied — and the plans are simply expensive: `2221` spent $91,376 against an
expected $42,531, and `2244` about half its optimality score. These are the only two
failures where the model did everything it was asked and still lost.

## MISSING_DOC — 1 of 23

`2290` (reward 22.9): three `po_origin_traceability` failures and an
`assembly_capacity_compliance` failure. The instruction requires the SO reference in each
PO's `origin` field, and the orders were created without it. This is a stated task
requirement rather than a generic ERP invariant, so it belongs to an agent-registered
`Rule`, not to `check.invariants`.

## OVERSPEND — 1 of 23

`2196` (reward 13.4): `budget_compliance` and `sale_revenue` fail alongside the timing
rules. A per-customer pretax cap stated in the instruction was exceeded — again a
task-stated rule, not a generic invariant.

## What this says

1. **The stock harness fails on feasibility arithmetic, not on Odoo mechanics.** No
   SUPPLIER, DRAFT, BOM_MATH, API_ERR, TOOL_LOOP or STEP_CAP failures at all. It can drive
   Odoo; it cannot reliably check that a plan's dates work before committing to them.
2. **Optimality is not the problem.** 2 of 23 failures (8.7%) are SUBOPTIMAL. Against
   PLAN.md's 40% threshold this is decisive: the planning section and a preinstalled
   solver are not what this benchmark is losing on, even though 183 of the 300 tasks carry
   a `min_new_spend` objective. Getting a *feasible* plan is the binding constraint;
   getting the *cheapest* feasible plan is a second-order concern that only two dev tasks
   ever reached.
3. **Failures cost more than passes.** The 23 failures average 34 steps against 22 for
   passes, and the same pattern held on eval100 where 72% of all spend went to trials that
   failed. A harness that detects infeasibility earlier is cheaper as well as better.
