# C_full v1 on eval100 — the 28 failures

Rule names come from the verifier's own output for our runs (`reward.json`); no eval task's tests or solution were read.

| cause | tasks | of which pi passed | scored ≥ 90 |
|---|---|---|---|
| optimality only | 12 | 3 | 6 |
| cap/timeout | 7 | 0 | 1 |
| origin traceability | 4 | 0 | 2 |
| capacity | 3 | 1 | 0 |
| timing/feasibility | 2 | 0 | 0 |

| task | reward | pi | terminal | cause | detail |
|---|---|---|---|---|---|
| 2049_medium_06_screened_buy_only_mixed_seeded | 75.3 | 100.0 | finish | optimality only | all rules green |
| 2073_medium_08_single_bom_single_workcenter | 60.3 | 0.0 | finish | optimality only | all rules green |
| 2094_medium_10_single_bom_split_by_capacity | 75.5 | 22.7 | finish | optimality only | all rules green |
| 2108_medium_12_single_subassembly_lowest_cost | 84.5 | 0.0 | finish | optimality only | all rules green |
| 2139_medium_14_single_subassembly_shared_overflo | 81.3 | 20.0 | finish | optimality only | all rules green |
| 2166_hard_17_shared_component_subassemblies_bran | 92.1 | 100.0 | finish | optimality only | all rules green |
| 2169_hard_17_shared_component_subassemblies_bran | 97.2 | 0.0 | finish | optimality only | all rules green |
| 2216_medium_21_single_bom_lowest_cost_screened_m | 94.0 | 100.0 | finish | optimality only | all rules green |
| 2225_medium_22_single_bom_split_by_capacity_invo | 98.2 | 89.6 | finish | optimality only | all rules green |
| 2234_hard_23_restricted_subassembly_qualified_wo | 80.6 | 20.8 | finish | optimality only | all rules green |
| 2247_hard_24_single_subassembly_shared_overflow_ | 97.1 | 97.1 | finish | optimality only | all rules green |
| 2251_hard_25_shared_component_subassemblies_bran | 94.7 | 22.7 | finish | optimality only | all rules green |
| 2098_hard_11_restricted_subassembly_qualified_wo | 19.6 | 19.1 | token_cap | cap/timeout | token_cap |
| 2141_hard_15_parallel_subassemblies_branch_assig | 23.1 | 0.0 | token_cap | cap/timeout | token_cap |
| 2148_hard_15_parallel_subassemblies_branch_assig | 23.1 | 0.0 | token_cap | cap/timeout | token_cap |
| 2155_hard_16_serial_subassemblies_branch_assigne | 81.6 | 0.0 | token_cap | cap/timeout | token_cap |
| 2164_hard_17_shared_component_subassemblies_bran | 0.0 | 0.0 | token_cap | cap/timeout | token_cap |
| 2229_hard_23_restricted_subassembly_qualified_wo | 95.1 | 20.4 | timeout | cap/timeout | timeout |
| 2282_medium_repair_plan_medium | 21.4 | 98.4 | timeout | cap/timeout | timeout |
| 2021_easy_03_buy_only_fixed_downpayment | 97.5 | 97.5 | finish | origin traceability | po_origin_traceability |
| 2087_medium_10_single_bom_split_by_capacity | 22.5 | 54.6 | finish | origin traceability | component_vendor_max_qty_compliance,po_origin_traceability |
| 2184_medium_19_manufacture_only_no_buy_route | 96.2 | 94.8 | finish | origin traceability | po_origin_traceability |
| 2293_hard_repair_plan_hard | 85.1 | 21.0 | finish | origin traceability | mrp_origin_traceability |
| 2067_medium_08_single_bom_single_workcenter | 20.0 | 0.0 | finish | capacity | finished_stock_capacity_compliance,spend_floor,supply_coverage |
| 2288_medium_repair_plan_medium | 17.5 | 24.4 | finish | capacity | finished_stock_capacity_compliance,supply_coverage,supply_timing_feasible |
| 2297_hard_repair_plan_hard | 20.4 | 99.1 | finish | capacity | finished_stock_capacity_compliance,supply_coverage,supply_timing_feasible |
| 2102_hard_11_restricted_subassembly_qualified_wo | 19.6 | 19.1 | finish | timing/feasibility | mo_component_feasibility,spend_floor,supply_timing_feasible |
| 2105_hard_11_restricted_subassembly_qualified_wo | 20.4 | 20.4 | finish | timing/feasibility | mo_component_feasibility,spend_floor,supply_timing_feasible |

## Reading

- **Optimality only** (12): every constraint and hygiene rule green; the plan's spend or objective sits off the reference by a few percent. Nine scored above 90. The `cheapest_buy` split and the objective line landed late in the run and did not close this tail on hard sub-assembly tasks.
- **Token cap** (5) and **timeouts** (2): all hard tasks; long reasoning steps against a 3M-input cap and the task's hour.
- **Origin traceability** (4), **capacity** (3), **timing** (2): the rule families the library models; residual misses are on multi-level sub-assembly plans.
