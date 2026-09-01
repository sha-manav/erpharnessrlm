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
- P1.2 Config A on eval100    | blocked| 2026-09-01 | first attempt INVALID (OpenRouter ran out of credit mid-run, 66/100 trials got HTTP 402); needs account funded, then rerun both models
- P1.3 Ingest + leaderboard   | todo   |            |
- P1.4 Stock failure analysis | todo   |            |

## Phase 2 — Harness v0
- P2.1 Kernel                 | done   | 2026-09-01 | TCP kernel in-container + host client; persistent namespaces, timeout rebuild, lib preload; 9 live tests
- P2.2 lib/fmt.py             | done   | 2026-09-01 | Table (40-row cap, Odoo shapes) + PageStore (4k threshold, 3k pages); 13 unit tests
- P2.3 lib/erp.py             | done   | 2026-09-01 | typed client over stdlib xmlrpc; PO/MO/SO flows pass live; found 5 Odoo-19 silent-wrong-state traps (NOTES)
- P2.4 lib/check.py + finish  | todo   |            |
- P2.5 lib/plan.py            | todo   |            |
- P2.6 Prompts                | todo   |            |
- P2.7 tools.py + loop.py     | todo   |            |
- P2.8 agent.py (Harbor)      | todo   |            |
- P2.9 v0 smoke + dev point   | todo   |            |

## Phase 3 — Harness v1
- P3.1 lib/db.py              | todo   |            |
- P3.2 lib/state.py           | todo   |            |
- P3.3 prompts/briefing.py    | todo   |            |
- P3.4 lib/delegate.py        | todo   |            |
- P3.5 Remaining invariants   | todo   |            |
- P3.6 Efficiency audit       | todo   |            |
- P3.7 Freeze checklist       | todo   |            |

## Phase 4 — Eval runs
- P4 all configs on eval100   | todo   |            |

## Phase 5 — Analysis and write-up
- P5.1 stats.py               | todo   |            |
- P5.2 figures.py             | todo   |            |
- P5.3 Failure taxonomy       | todo   |            |
- P5.4 Write-up               | todo   |            |
