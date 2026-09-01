# PROGRESS.md

One line per task id: `status | date | note`. Statuses: `todo`, `doing`, `done`, `blocked`.

## Phase 0 — Discovery
- P0.1 Harbor                 | doing  | 2026-08-31 | harbor 0.22.0 installed via uv; CLI flags + custom-agent interface recorded in NOTES
- P0.2 ERP-Bench dataset      | doing  | 2026-08-31 | repo cloned; 300 tasks, 29 patterns; env Dockerfile patched (PEP 668); container up
- P0.3 pi harness             | todo   |            |
- P0.4 Model endpoints        | blocked| 2026-08-31 | needs MODEL_BIG_API_KEY / MODEL_SMALL_API_KEY from user
- P0.5 Container abstraction  | todo   |            |

## Phase 1 — Reproduction and failure analysis
- P1.1 Select eval100 and dev | todo   |            |
- P1.2 Config A on eval100    | todo   |            |
- P1.3 Ingest + leaderboard   | todo   |            |
- P1.4 Stock failure analysis | todo   |            |

## Phase 2 — Harness v0
- P2.1 Kernel                 | todo   |            |
- P2.2 lib/fmt.py             | todo   |            |
- P2.3 lib/erp.py             | todo   |            |
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
