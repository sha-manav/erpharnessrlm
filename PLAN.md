# PLAN.md — ERP-Harness

You (Claude Code) are implementing a research project and you can push to sha-manav/erpharnessrlm:
you are building a domain-specific agent harness for ERP-Bench (300 verifiable Odoo 19
procurement/manufacturing tasks, Harbor format), evaluated against the generic pi coding-agent
harness used on the public leaderboard, with ablations and token/cost measurement. The
methodological reference is Harvey's harness work: the RLM harness that lifted GLM-5.2 from 15% to
46% on legal diligence with no training, the finish tool, the agentic-vs-single-turn retrieval
comparison, and the quality-vs-cost Pareto plot. **No fine-tuning in this project.**

Work through the phases in order. Every task has a *Done when* check; do not start a phase until
the previous phase's checks pass. Maintain two files from the start:

- **PROGRESS.md** — one line per task id: status, date, short note.
- **NOTES.md** — facts discovered about Harbor, ERP-Bench, pi, Odoo, and the model endpoints.
  Later phases reference NOTES.md fields by name.

When this plan contradicts what you find in the real repos, **the real repos win**. Record the
discrepancy in NOTES.md and adapt the interface, not the goal.

**Session start checklist (every session):** read PROGRESS.md and NOTES.md; run `make guard`;
confirm `git status` is clean or matches the in-progress task; pick the next task id; do not skip
ahead.

**Prerequisites:** Docker with compose, Python 3.11+, uv, git, enough RAM for N Odoo+Postgres task
containers in parallel (Odoo ~1.5 GB each; set N in NOTES.md after measuring), and outbound network
for the model endpoints.

## Hard rules

1. **Eval isolation.** Once `configs/eval100.txt` exists, never open, read, grep, cat, diff, or copy
   `tests/`, `solution/`, verifier, oracle, or grader files of any task on that list. Add a
   deny-list check (`scripts/guard.sh`) that fails CI if any such path is touched in a commit.
2. **Validators come from domain knowledge.** `harness/lib/check.py` is derived from Odoo/ERP
   practice (Appendix B), never from benchmark grader code — not even dev tasks'.
3. **No pattern-specific logic.** No code paths, prompts, or branches keyed to task patterns or
   instruction phrasings anywhere under `harness/`. Before each freeze, grep for pattern names from
   the task metadata and remove any hits.
4. **Dev-only iteration.** Develop and tune only on `configs/dev.txt` (or minted instances). Eval
   runs happen only in Phases 4–5, only from a tagged commit with a clean tree.
5. `eval100` is immutable after task P1.1 completes.
6. **Secrets come from environment variables** (`MODEL_BIG_API_KEY`, `MODEL_SMALL_API_KEY`). Never
   commit them. Redact Authorization headers from logs.
7. **Long runs go to the background** (`nohup … > runs/<id>/stdout.log &` or tmux). Poll with tail;
   never block a session on a multi-hour run.
8. Every run directory has `meta.json`: git commit, dirty flag, config id, model id, caps, seed,
   start/end timestamps, terminal reason per task.
9. **Same everything across configs:** task images, Odoo version, model versions, temperature, step
   cap. A change to any of these after a run means the run is invalid and must be redone.

## Repo layout

```
erp-harness/
  PLAN.md  PROGRESS.md  NOTES.md  README.md  Makefile  pyproject.toml
  configs/
    models.yaml        # endpoints, temperature, caps, pricing
    configs.yaml       # harness configurations A, B, C, ablations
    eval100.txt        # frozen eval task ids (Phase 1)
    dev.txt            # dev task ids
  harness/
    agent.py           # Harbor entrypoint (custom agent) or host-side driver
    loop.py            # the LLM loop: messages, tools, caps, paging, ledger, logging
    llm.py             # OpenAI-compatible client, usage accounting, retries
    container.py       # exec/put_file abstraction over the task container
    kernel_server.py   # persistent Python kernel that runs INSIDE the task container
    lib/               # library preloaded into the kernel
      __init__.py erp.py db.py state.py check.py plan.py delegate.py finish.py fmt.py
    prompts/
      contract.md contract_min.md playbook.md schema_card.md library_docs.md briefing.py
    tools.py           # JSON tool schemas exposed to the model
  scripts/
    discover.py select_eval100.py run.py ingest_harbor.py aggregate.py stats.py figures.py
    failure_taxonomy.py guard.sh
  analysis/            # dev_curve.csv, failures_*.md, results.csv, figures/
  tests/               # pytest against a live dev-task container
  runs/                # gitignored
```

Makefile targets: `setup`, `discover`, `select`, `run CONFIG= MODEL= SET=`, `ingest`, `aggregate`,
`stats`, `figs`, `guard`, `test`, `docs`.

## Phase 0 — Discovery (fills NOTES.md)

Everything below is "find out and record". Do not build anything in this phase beyond
`scripts/discover.py` helpers.

**P0.1 Harbor.** Install Harbor per harborframework.com docs. Run `harbor --help`,
`harbor run --help`. Read the docs on datasets, agents, and custom agents. Record in NOTES.md
(`## Harbor`): flags for dataset, agent selection, model selection, task filtering (by id / list
file), concurrency, timeout, output directory; how a custom agent is provided (base class, import
path flag, required methods, how it receives the instruction, how it gets shell access to the task
container, how it signals completion); where Harbor writes per-task results and trajectories, and
the result JSON schema (reward field name, pass field name); whether the agent runs inside the task
container or on the host driving it.
*Done when:* all fields filled and a `harbor run` on one dev task with any built-in agent produces a
result file you can locate and parse.

**P0.2 ERP-Bench dataset.** Do: `harbor pull`/download `agentic-labs/erp-bench`; find the local task
directories; open one task whose id you will place in `dev.txt` (pick it now, note it, and never use
it for eval). Record (`## ERP-Bench`): task directory layout (instruction file name,
environment/compose files, metadata file, where the verifier and solution live — record the paths so
the guard script can deny them); metadata field that names the task pattern (needed for
stratification); count of tasks per pattern; reward semantics: run the verifier on the untouched
environment and on the oracle solution for this one dev task; record reward range, what value counts
as pass, whether per-rule flags are exposed (do this by running the verifier and reading its output,
not by studying its rule logic); Odoo access from the agent's container (hostname/port, database
name, admin login/password and/or API key, whether XML-RPC and JSON-RPC are exposed); Postgres
access (host, port, user, password, CREATEDB, whether psql/psycopg2 exist); what tooling the agent
container has and whether it has outbound network; whether the Anchor generator ships in the repo
and how to mint N instances with a seed (`## Minting`).
*Done when:* you can, from a shell inside the agent container of the dev task, authenticate to Odoo
and read `sale.order` records, and run one SELECT against Postgres.

**P0.3 pi harness.** Clone the pi repo. Read its source end to end. Record (`## pi`): tool set
(exact names and behaviors), system prompt text (copy into NOTES.md verbatim), loop structure,
step/turn cap, temperature, max_tokens, timeouts, output truncation behavior; how it is run under
Harbor and how "coding" mode connects to Odoo; which model-provider API it speaks.
*Done when:* `harbor run` with pi on the dev task from P0.2 completes and you can find the
trajectory and reward.

**P0.4 Model endpoints.** For each of the two models (big: an API-served open model that appears on
the leaderboard if still served; small: a ~30B model on vLLM or API), hit the OpenAI-compatible
`/chat/completions` with tools and record the usage object fields, tool-calling format quirks, and
rate limits. Record (`## Models`) and fill `configs/models.yaml`.
*Done when:* a scripted 2-turn tool-call round trip works for both models and usage fields parse.

**P0.5 Container abstraction.** Based on P0.1, decide which mode `harness/container.py` implements:
(a) in-container agent; (b) host-side driver. Implement `Container.exec(cmd, timeout, stdin=None)
-> (rc, stdout, stderr)` and `Container.put_file(path, bytes)`.
*Done when:* `put_file` + `exec("python3 -c ...")` round-trips inside a live dev-task container.

## Phase 1 — Reproduction and failure analysis

**P1.1 Select eval100 and dev.** `scripts/select_eval100.py`: read all task metadata, group by
pattern, seed=0, sample 3–4 per pattern to reach exactly 100 (fill remainder by round-robin over
patterns), write `configs/eval100.txt` (sorted ids) and `configs/dev.txt` (the remaining 200). Also
write `configs/dev40.txt` (40 dev tasks, stratified, seed=2 — the dev-curve set), `configs/dev10.txt`
(first 10 of dev40), and `configs/dev5.txt` (first 5). If minting is available, mint 60 instances
with seed=1 into `data/minted/`, list them in `configs/dev_minted.txt`, and build dev40/dev10/dev5
from minted instances instead; prefer minted instances for development. Then create
`scripts/guard.sh`. *Done when:* eval100 has 100 unique ids covering every pattern; `make guard`
passes; commit tagged `eval100-frozen`.

**P1.2 Run config A (stock pi) on eval100, both models.**
*Done when:* both runs complete with per-task results; meta.json written.

**P1.3 Ingest and compare to leaderboard.** Acceptable: within ±8 points.
*Done when:* agreement within tolerance, recorded in NOTES `## Reproduction`.

**P1.4 Failure analysis of the stock harness (decides build order).** Run config A with big on
`configs/dev40.txt`; code up to 30 failures; write `analysis/failures_stock_big.md` and
`analysis/build_order.md`. Default order if evidence is mixed: P1 client + P8 playbook → P4
validators + P7 finish → P5 ledger → P9 briefing → P3 snapshots → P6 delegation. If ≥40% of failures
are SUBOPTIMAL, add an explicit planning section to the playbook and make sure ortools/pulp are
installable in the kernel.
*Done when:* both files exist and the build order is fixed for Phase 2–3.

## Phase 2 — Harness v0 (safe layer)

**P2.1 Kernel.** `harness/kernel_server.py` runs inside the task container: JSON lines
`{"id":…, "code":…, "timeout": 120}`, persistent namespace, captures stdout/stderr, returns
`{"id":…, "ok": bool, "stdout":…, "stderr":…, "elapsed":…}`. On timeout: kill, restart namespace,
return ok=false with "kernel restarted; in-memory state lost". Optional `"ns"` namespace id
(default "root") so sub-agents get isolated state. Every namespace preloaded with `harness/lib`.
*Done when:* `tests/test_kernel.py` passes.

**P2.2 lib/fmt.py — tables and paging.** `Table` (max 40 rows then `…`), `PageStore` (>4,000 chars
stored under handle `h<N>`, model sees first 3,000 chars, `show(handle, page)`).
*Done when:* unit tests pass.

**P2.3 lib/erp.py — typed Odoo client.** Read API (`sales_orders`, `so_lines`, `stock`, `boms`,
`suppliers`, `purchase_orders`, `po_lines`, `productions`, `pickings`, `invoices`, `get`,
`search_read`, `fields`, `call`) and Write API (`create_po`, `confirm_po`, `receive`, `create_mo`,
`confirm_mo`, `produce`, `deliver`, `invoice`, `post`, `cancel`, `_validate_picking`). Every
mutating call appends to `erp.write_log`. Errors → `OdooError` with the last 600 chars of the Odoo
error text.
*Done when:* `tests/test_erp.py` passes the full PO / MO / SO flows against a live dev container.

**P2.4 lib/check.py v0 and lib/finish.py.** `Check = (id, title, hard, fn)`;
`check.invariants(client)`; `Rule`; `check.rules`; `check.all`; `check.register`. `finish(summary)`
runs `check.all()`; hard failures refuse up to 3 attempts, then write `/output/summary.md` and
return FINISHED.
*Done when:* `tests/test_check.py` detects every seeded violation and passes a clean state.

**P2.5 lib/plan.py — ledger.** `plan.set/add/update/show/summary`. *Done when:* unit tests pass.

**P2.6 Prompts.** `contract.md` (≤250 words), `contract_min.md`, `playbook.md`, `schema_card.md`,
`library_docs.md` (generated), `briefing.py`.
*Done when:* config C system prompt < 9,000 tokens and contract_min < 400 tokens.

**P2.7 harness/tools.py and harness/loop.py.** Tools `python`, `bash`, `show`, `finish`. Static
system prefix for prefix caching; append-only messages; step cap (default pi's cap else 150); token
cap 2.5M → `token_cap`; ledger reinjection every K=15 steps; loop detection on 3 identical calls;
retries with backoff (max 5) → `api_error`; `trajectory.jsonl` logging.
*Done when:* `tests/test_loop.py` with a mocked LLM passes.

**P2.8 harness/agent.py — Harbor integration.** Copy kernel + lib into the container, export
Odoo/PG env vars, start kernel, snapshot, briefing, run loop. Configs A_pi, B_bash, C_full,
C_minus_dry, C_minus_doc, C_minus_del, C_minus_brief, B_plus_doc.
*Done when:* `make run CONFIG=C_full MODEL=big SET=dev5` completes end to end.

**P2.9 v0 smoke and first dev point.** Run C_full and B_bash on dev40 with big; append to
`analysis/dev_curve.csv`. *Done when:* v0 mean reward on dev40 ≥ config A's from P1.4.

## Phase 3 — Harness v1 (higher-ceiling layer)

**P3.1 lib/db.py — read-only SQL** (`ro_agent` role, SELECT/WITH only, auto LIMIT).
**P3.2 lib/state.py — snapshots and dry run** (`CREATE DATABASE … TEMPLATE`, `state.diff`,
`state.drop`, `state.list`; fallback P3.2b record-and-replay `state.promote`).
**P3.3 prompts/briefing.py** (≤1,800 tokens, generic queries only).
**P3.4 lib/delegate.py** (sub-loop, python only, `ErpReadOnly`, ≤400-token return,
`agent_id="sub-<n>"`).
**P3.5 Remaining invariants** (Appendix B items 2, 5, 6, 8, 9, 10).
**P3.6 Efficiency instrumentation audit** (`scripts/cost.py`).
**P3.7 Freeze checklist** — `make guard`, `make test`, `make docs`; grep for pattern names; tag
`v1.0`; record in NOTES `## Freeze`.

## Phase 4 — Eval runs

Only from tag `v1.0`, clean tree. Order: C_full (big, small) → B_bash (big, small) → C_minus_dry
(big, small) → B_plus_doc (big, small) → C_minus_doc, C_minus_del, C_minus_brief. After each run:
`make ingest`, `make aggregate`, sanity check (no `api_error` > 5%).

## Phase 5 — Analysis and write-up

**P5.1 scripts/stats.py** — McNemar exact + paired bootstrap 95% CI (10,000 resamples, seed 0) →
`analysis/stats.md`.
**P5.2 scripts/figures.py** — `fig2_harness_groups.png`, `fig1_pareto.png`, `fig_tokens.png`,
`fig_dev_curve.png`, `table_ablations.md`.
**P5.3 Failure taxonomy for C_full** → `analysis/failures_C_full.md`.
**P5.4 Write-up** → `analysis/writeup.md`.

## Common schema

`runs/<run_id>/<task_id>/trajectory.jsonl` — one object per event:

```json
{"t": 12, "agent_id": "root", "role": "assistant|tool|user|system", "content": "...",
 "tool": "python|bash|show|finish|null", "args": {}, "output": "...",
 "usage": {"input": 0, "cached": 0, "output": 0}, "latency_s": 0.0, "ts": "ISO8601"}
```

`runs/<run_id>/<task_id>/result.json`:

```json
{"task_id": "...", "config": "...", "model": "...", "commit": "...", "pass": true, "reward": 0.0,
 "steps": 0, "delegations": 0, "tokens": {"input": 0, "cached": 0, "output": 0}, "cost_usd": 0.0,
 "wallclock_s": 0.0, "terminal_reason": "finish|step_cap|token_cap|api_error|loop|crash",
 "lib_active": ["erp", "db"], "verifier_raw": {}}
```

`analysis/results.csv` columns: task_id, pattern, config, model, pass, reward, steps, delegations,
tokens_in, tokens_cached, tokens_out, cost_usd, wallclock_s, terminal_reason, commit.

## Failure taxonomy (one primary code per failed trajectory)

SUPPLIER · TIMELINE · MISSING_DOC · DRAFT · OVERSPEND · SUBOPTIMAL · BOM_MATH · API_ERR ·
TOOL_LOOP · STEP_CAP · PREMATURE_FINISH · SCOPE · OTHER

## Appendix A — Playbook content (Odoo 19)

**Data model.** `product.product`/`product.template`; `product.supplierinfo` (partner_id, min_qty,
price, delay); `mrp.bom`/`mrp.bom.line`; `sale.order`/`sale.order.line` (commitment_date);
`purchase.order`/`purchase.order.line` (date_planned); `mrp.production` (product_qty, bom_id,
date_start, qty_producing); `stock.picking`/`stock.move`; `stock.quant` (quantity,
reserved_quantity, internal locations only); `account.move` (move_type, state).

**Flows.** Sales: `action_confirm` → delivery picking → `action_assign` → set quantities →
`button_validate` → invoice via `sale.advance.payment.inv` (`create_invoices`) → `action_post`.
Purchasing: create PO lines with `price_unit` from supplierinfo and `date_planned` →
`button_confirm` → receipt picking → validate. Manufacturing: create MO → `action_confirm` →
`action_assign` → `qty_producing = product_qty` → `button_mark_done`.

**Gotchas.** Underscore methods are not RPC-callable (use wizards); pickings need quantities before
validation; Many2one writes take an id, One2many writes take command tuples `(0, 0, vals)`;
datetimes are UTC strings; names come from `ir.sequence`; components must be available before an
MO's start; receipt date = order date + supplierinfo.delay; do not hardcode ids obtained during a
dry run.

**Planning section** (include if P1.4 found SUBOPTIMAL failures). Enumerate every feasible sourcing
option per shortage, compute total landed cost and earliest completion date for each, compare
against every due date, then choose. Write the comparison as a table before acting.

## Appendix B — Generic end-state invariants (check.invariants)

1. **No dangling drafts** (hard). Every SO/PO/MO created during the task is confirmed/done or
   cancelled.
2. **Demand covered** (hard). Every in-scope customer order has a delivery that is done, or
   scheduled on/before its due date with stock reserved.
3. **Stock non-negative** (hard) for every product and internal location.
4. **Supplier validity** (hard). Each PO line's vendor is listed in supplierinfo for that product;
   `product_qty ≥ min_qty`; `price_unit` consistent with the pricelist tier.
5. **Timeline feasibility** (hard). Each receipt's `date_planned` ≤ the need date of the demand it
   feeds, given delay.
6. **BOM arithmetic** (hard). MO quantity × BOM = component demand; components sourced before MO
   start; MO quantity covers the shortage without unexplained over-production.
7. **Invoicing** (hard). Delivered orders invoiced; invoices posted; totals match.
8. **No duplicates** (soft). No two POs/MOs cover the same need.
9. **Spend tally** (soft, informational unless a registered rule sets a cap).
10. **Scope containment** (soft). Diff from start touches only task-related records.

## Appendix C — System prompt contract (config C)

1. Read the instruction. Put every rule and constraint into `plan.set([...])` and encode the
   checkable ones as Rules via `check.register(...)` before acting.
2. Investigate on main, read-only (`erp` reads, `db.sql`). Delegate independent investigations with
   `delegate(...)`; sub-agents cannot write.
3. Write the plan as a re-runnable function `plan(client)`. `state.snapshot("s1")`; run
   `plan(erp.on("s1"))`; run `check.all(erp.on("s1"))`; fix and repeat until clean.
4. Run `plan(erp)` on main; run `check.all()`; confirm `state.diff` matches the dry run; call
   `finish(summary)`. `finish` refuses while hard checks fail.
