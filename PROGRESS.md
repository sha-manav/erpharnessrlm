# PROGRESS.md

One line per task id: `status | date | note`. Statuses: `todo`, `doing`, `done`, `blocked`.

## Phase 0 — Discovery
- P0.1 Harbor                 | done   | 2026-08-31 | harbor 0.22.0; CLI flags, custom-agent interface, result schema in NOTES; nop run on dev task parses
- P0.2 ERP-Bench dataset      | done   | 2026-08-31 | 300 tasks/29 patterns; dev task 2000_easy_01_buy_only_baseline; nop=0 oracle=100; Odoo XML-RPC+JSON2 and PG SELECT verified; minting verified
- P0.3 pi harness             | done   | 2026-08-31 | pi 0.84.4 via npm; 4 tools, prompt verbatim, no turn cap, temp unset; dev-task run: 100.0/passed, 20 turns, $0.143
- P0.4 Model endpoints        | done   | 2026-08-31 | OpenRouter GLM-5.1 (big) + Qwen3-32B (small); 2-turn tool round trip OK; usage/cost/provider fields recorded
- P0.5 Container abstraction  | done   | 2026-08-31 | host-side driver; DockerContainer round-trip verified on erpdev; HarborContainer bridges in P2.8

## Phase 1 — Reproduction and failure analysis
- P1.1 Select eval100 and dev | done   | 2026-08-31 | eval100 frozen: 100 ids, 29 patterns, 3-4 each; dev 200 / dev40 / dev10 / dev5; guard armed; tag eval100-frozen
- P1.2 Config A on eval100    | done   | 2026-09-01 | big 100/100 (3 fp8-only batches); small stopped at 67/100 (0 passes, floor result)
- P1.3 Ingest + leaderboard   | done   | 2026-09-01 | GLM-5.1 39.0 vs 35.8 published (+3.2, within +/-8); analysis/reproduction.md
- P1.4 Stock failure analysis | done   | 2026-09-02 | dev40 A_pi 42.5% pass, 23 failures coded: 17 TIMELINE, 2 PREMATURE_FINISH, 2 SUBOPTIMAL (8.7%, below the 40% trigger), 1 MISSING_DOC, 1 OVERSPEND

## Phase 2 — Harness v0
- P2.1 Kernel                 | done   | 2026-09-01 | TCP kernel in-container + host client; persistent namespaces, timeout rebuild, lib preload; 9 live tests
- P2.2 lib/fmt.py             | done   | 2026-09-01 | Table (40-row cap, Odoo shapes) + PageStore (4k threshold, 3k pages); 13 unit tests
- P2.3 lib/erp.py             | done   | 2026-09-01 | typed client over stdlib xmlrpc; PO/MO/SO flows pass live; found 5 Odoo-19 silent-wrong-state traps (NOTES)
- P2.4 lib/check.py + finish  | done   | 2026-09-01 | Appendix B 1/3/4/7 + finish gate (3 refusals); 12 live tests; found Odoo auto-adds vendors on PO confirm
- P2.5 lib/plan.py            | done   | 2026-09-01 | ledger with bounded summary for reinjection; 9 unit tests
- P2.6 Prompts                | done   | 2026-09-01 | contract 425t, playbook 1168t, schema_card 694t, library_docs 1167t (generated) = 3454t < 9000; contract_min 175t < 400
- P2.7 tools.py + loop.py     | done   | 2026-09-01 | tools.py schemas, llm.py (explicit cache breakpoints, provider pinning, retries), loop.py caps/paging/ledger/loop-detection; 17 unit tests
- P2.8 agent.py (Harbor)      | done   | 2026-09-02 | C_full and B_bash dev5 smokes both complete cleanly (finish:5); 6 bugs found and fixed via smokes
- P2.9 v0 smoke + dev point   | doing  | 2026-09-02 | checkpoint pass 1: 2/3 at 100, cache 39% (Chutes) + timeline false negative found and fixed; helpers feasible_vendors/earliest_build/rehearse added; pass 2 next, then dev40

## Phase 3 — Harness v1
- P3.1 lib/db.py              | done   | 2026-09-02 | read-only SQL enforced by a Postgres ro_agent role, not by string matching; 5 live tests
- P3.2 lib/state.py           | done   | 2026-09-02 | CREATE DATABASE TEMPLATE snapshots reachable over RPC, isolated from main; diff/list/drop; 4 live tests
- P3.3 prompts/briefing.py    | done   | 2026-09-02 | lib/brief.py: per-section budgets, ~1.9k tokens, generic queries; injected into the first user message
- P3.4 lib/delegate.py        | todo   |            |
- P3.5 Remaining invariants   | part   | 2026-09-02 | items 2/5/6 promoted early (74% of failures): demand_covered, timeline_feasible, mo_feasible. Items 8/9/10 remain
- P3.6 Efficiency audit       | todo   |            |
- P3.7 Freeze checklist       | todo   |            |

## Phase 4 — Eval runs
- P4 all configs on eval100   | todo   |            |

## Phase 5 — Analysis and write-up
- P5.1 stats.py               | todo   |            |
- P5.2 figures.py             | todo   |            |
- P5.3 Failure taxonomy       | todo   |            |
- P5.4 Write-up               | todo   |            |
