# Reproduction of the published ERP-Bench numbers (P1.3)

Config A is Harbor's built-in `pi` agent (pinned to npm 0.84.4), the same generic
coding-agent harness the Anchor paper used, on the frozen 100-task subset
(`configs/eval100.txt`). Development trials are excluded.

| config | model | n | pass@1 | published | Δ | mean reward | mean steps | $/task |
|---|---|---|---:|---:|---:|---:|---:|---:|
| A_pi | z-ai/glm-5.1 | 100 | 39.0 | 35.8 | +3.2 | 54.7 | 27.7 | 0.624 |
| A_pi | qwen/qwen3-32b | 67 | 0.0 | — | — | 1.2 | 15.0 | 0.007 |
| B_bash | z-ai/glm-5.1 | 30 | 33.3 | 35.8 | -2.5 | 52.8 | 34.4 | 0.841 |
| C_full | z-ai/glm-5.1 | 100 | 72.0 | 35.8 | +36.2 | 88.9 | 33.5 | 0.829 |

## Verdict

- **A_pi / z-ai/glm-5.1**: 39.0 against 35.8 published, a gap of 3.2 points — within the ±8-point tolerance.
- 4 of those A_pi/z-ai/glm-5.1 trials were **interrupted** (terminal reason timeout/crash) when a run was stopped by hand rather than by the agent's own failure: `2067_medium_08_single_bom_single_workcenter`, `2074_medium_08_single_bom_single_workcenter`, `2149_hard_15_parallel_subassemblies_branch_assigned`, `2174_medium_18_manufacture_only_policy_forbidden`. Their verifier still scored whatever state existed at the kill, which biases the figure down. Excluding them: **40.6** over 96 trials. Re-running those tasks cleanly would remove the caveat.
- 1 of those A_pi/qwen/qwen3-32b trials were **interrupted** (terminal reason timeout/crash) when a run was stopped by hand rather than by the agent's own failure: `2091_medium_10_single_bom_split_by_capacity`. Their verifier still scored whatever state existed at the kill, which biases the figure down. Excluding them: **0.0** over 66 trials. Re-running those tasks cleanly would remove the caveat.
- **B_bash / z-ai/glm-5.1**: 33.3 against 35.8 published, a gap of 2.5 points — within the ±8-point tolerance.
- **C_full / z-ai/glm-5.1**: 72.0 against 35.8 published, a gap of 36.2 points — OUTSIDE the ±8-point tolerance.
- 2 of those C_full/z-ai/glm-5.1 trials were **interrupted** (terminal reason timeout/crash) when a run was stopped by hand rather than by the agent's own failure: `2282_medium_repair_plan_medium`, `2229_hard_23_restricted_subassembly_qualified_workcenters_screened_all_seeded`. Their verifier still scored whatever state existed at the kill, which biases the figure down. Excluding them: **73.5** over 98 trials. Re-running those tasks cleanly would remove the caveat.

## What differs from the published setup

| | Anchor Table 12 | here |
|---|---|---|
| tasks | all 300 | frozen 100, stratified over all 29 patterns |
| trials per task | 5 | 1 |
| harness | pi-mono toolkit | pi 0.84.4 (`@earendil-works/pi-coding-agent`), same CLI and tools |
| turn budget | 400 | none enforced — pi 0.84.4 has no `--max-turns` |
| timeout | 1 h | 1 h (`[agent] timeout_sec = 3600`) |

Sampling noise alone puts a 1-trial, 100-task estimate of a ~36% rate at about
±4.8 points (1 SE), so agreement inside a couple of points is as close as this
design can resolve.

## Per-pattern breakdown

| pattern | n | pass@1 |
|---|---:|---:|
| 01_buy_only_baseline | 3 | 67 |
| 02_buy_only_immediate_invoicing | 3 | 33 |
| 03_buy_only_fixed_downpayment | 3 | 67 |
| 04_buy_only_percentage_downpayment | 3 | 100 |
| 05_screened_buy_only_all_seeded | 3 | 67 |
| 06_screened_buy_only_mixed_seeded | 4 | 75 |
| 07_screened_buy_only_mixed_seeded_invoicing | 4 | 100 |
| 08_single_bom_single_workcenter | 4 | 0 |
| 09_single_bom_lowest_cost | 4 | 0 |
| 10_single_bom_split_by_capacity | 4 | 50 |
| 11_restricted_subassembly_qualified_workcenters | 3 | 0 |
| 12_single_subassembly_lowest_cost | 4 | 50 |
| 13_single_subassembly_qualified_workcenters | 4 | 0 |
| 14_single_subassembly_shared_overflow_capacity | 4 | 0 |
| 15_parallel_subassemblies_branch_assigned | 3 | 0 |
| 16_serial_subassemblies_branch_assigned | 3 | 33 |
| 17_shared_component_subassemblies_branch_assigned | 4 | 25 |
| 18_manufacture_only_policy_forbidden | 4 | 50 |
| 19_manufacture_only_no_buy_route | 3 | 33 |
| 20_manufacture_only_no_available_vendors | 4 | 50 |
| 21_single_bom_lowest_cost_screened_mixed_seeded | 4 | 50 |
| 22_single_bom_split_by_capacity_invoicing | 4 | 25 |
| 23_restricted_subassembly_qualified_workcenters_screened_all_seeded | 3 | 0 |
| 24_single_subassembly_shared_overflow_capacity_screened_invoicing | 3 | 67 |
| 25_shared_component_subassemblies_branch_assigned_screened_all_seeded | 3 | 0 |
| 26_buy_only_net_30_no_adjacent_data | 3 | 100 |
| repair_plan_easy | 3 | 33 |
| repair_plan_hard | 3 | 33 |
| repair_plan_medium | 3 | 33 |
