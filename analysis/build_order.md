# Phase 3 build order (P1.4 output)

Ranked by how many of the 23 coded stock-harness failures each primitive would plausibly
prevent (`analysis/failures_stock_big.md`). This supersedes PLAN.md's default order, which
was written before the evidence existed.

The evidence in one line: **17 of 23 failures are TIMELINE** — plans that are correct in
quantity, vendor and structure, and wrong in dates. Nothing in the stock harness computes,
or checks, whether a receipt lands before the demand it feeds.

## Order

| # | primitive | failures addressed | why |
|---|---|---:|---|
| 1 | **P3.5 invariants 5 + 6** (timeline feasibility, BOM arithmetic) | **17** | Appendix B.5 is literally the failing rule: every receipt's `date_planned` ≤ the need date of the demand it feeds, given `supplierinfo.delay`. B.6 is the manufacturing half — components sourced before MO start. These two checks convert the dominant failure from a lost trial into a refused `finish`. |
| 2 | **P3.2 state snapshots + dry run** | 17 + 2 | A check only helps if there is somewhere to fail safely. Rehearsing `plan(erp.on("s1"))` and running `check.all()` on the clone is what turns "your dates are infeasible" into a corrected plan rather than a broken database. Multiplies primitive 1; nearly worthless without it. |
| 3 | **P3.3 briefing** | 17 (prevention) | The five throwaway `gather_info` scripts in `2109` exist because nothing hands the model due dates, vendor lead times and stock up front. Front-loading them attacks the same failure earlier and should cut steps, which is where the cost is. |
| 4 | P3.1 `db.py` read-only SQL | ~0 directly | No coded failure was caused by not being able to query. Useful for the agent's own arithmetic over many orders; keep, but late. |
| 5 | P3.4 `delegate` | ~0 directly | No failure looks like under-delegation. Harvey's gain came from fixing that on a task shape where retrieval dominates; here the bottleneck is a date comparison, not breadth. Build it for the ablation, expect little. |

Already built and validated in Phase 2: the typed client (P1), the playbook (P8), the
validators and finish gate (P4/P7), the ledger (P5). The finish gate matters more than its
rank suggests — it is what makes primitive 1 bite, and it directly addresses the 2
PREMATURE_FINISH failures.

## Two decisions PLAN.md asks for explicitly

**Planning section in the playbook: NO (trimmed, not added).** PLAN.md says add an explicit
cost-planning section if ≥40% of failures are SUBOPTIMAL. The measured figure is
**2 of 23 = 8.7%**, so the trigger is not met. This is worth stating plainly because it is
counter-intuitive: 183 of the 300 tasks carry a `min_new_spend` objective, so the benchmark
*looks* like an optimisation benchmark. It is not losing on optimisation. Only two dev
tasks ever produced a valid end state whose only fault was cost; the other 21 never got a
feasible plan to optimise.

The playbook's existing planning text is therefore **retargeted at feasibility rather than
cost**: enumerate options and compute, for each, the *earliest completion date* alongside
landed cost, and compare against every due date. That serves the 17 TIMELINE failures. The
cost-comparison language stays but is secondary.

**Preinstall `ortools`: NO.** It is installable in the container in 5.7 s if a task ever
needs it (`pip3 install --break-system-packages ortools`), and on this evidence none do.
Preinstalling would add a dependency to the image for a failure mode worth 8.7% of
failures — and the two SUBOPTIMAL tasks lost on plan choice, not on solver power.

## What would change this

If C_full's own dev40 failures shift the mix — in particular if fixing TIMELINE exposes a
tail of SUBOPTIMAL failures that were previously masked — this ranking should be recomputed
from the C_full trajectories before Phase 3 continues. That is the expected outcome if
primitives 1–3 work: you cannot lose on cost until you can produce a feasible plan.
