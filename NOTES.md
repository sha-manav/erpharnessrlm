# NOTES.md — facts discovered

Everything here is measured from the real repos/tools, not assumed. When the plan and the
repos disagree, the repos win and the discrepancy is recorded under **Discrepancies**.

---

## Environment (this machine)

| Fact | Value |
|---|---|
| Host | macOS (darwin 25.5.0), aarch64, 18 CPUs, 48 GB RAM |
| Docker | 29.7.2, compose v5.4.0, Docker Desktop, arch aarch64 |
| **Docker Desktop VM memory** | **7.75 GiB** — the binding constraint, see *Concurrency* |
| Python | 3.14.7 (system), 3.11 available at /opt/homebrew/bin/python3.11 |
| uv | 0.12.8, installed to ~/.local/bin |
| harbor | 0.22.0 (`uv tool install harbor`), binaries `harbor`, `hb`, `hr` |
| Repo | /Users/manavshah/erpharnessrlm (git initialised 2026-08-31) |
| Vendored | vendor/pi (earendil-works/pi), vendor/erp-bench (agentic-labs/erp-bench) — both gitignored |

### Concurrency (N)

Each ERP-Bench task declares `cpus = 3`, `memory_mb = 4096`, but **measured** usage is far lower:

| State | Memory |
|---|---|
| idle, scenario loaded | **323 MiB** |
| under a live pi agent trial (Odoo serving RPC) | **508 MiB** |
| declared in task.toml | 4096 MiB |

Docker's `--memory` is a cap, not a reservation, so the 7.75 GiB VM is not limited to one trial.
Measured with **4 concurrent pi trials**: 437-467 MiB each (1.8 GiB of 7.75 GiB used), CPU 0.1-5%
per container — the agent phase is dominated by waiting on the model, not by Odoo. Images share
their base layers, so 6 task images cost 7 GB total, not 6 x 3.4 GB.

Working figure: **N = 8** is safe on the current 7.75 GiB VM; **N = 10-12** after raising Docker
Desktop to 32 GB. The memory bump is a comfort margin, not a prerequisite. Two caveats before trusting a higher N: (a) config A's per-trial `npm install && npm run
build` of pi-mono peaks well above the steady state, and (b) 18 host CPUs / 3 declared CPUs per
task also caps useful parallelism around 6. Re-measure with `docker stats` during the first
multi-task run and fix N here.

**Action for the operator:** Docker Desktop's `settings-store.json` is TCC-protected (not writable
from a terminal), so the memory bump must be made in **Docker Desktop -> Settings -> Resources**.
Note that the eight long-running `frappe_docker-*` containers on this machine have restart policy
`on-failure`, so a Docker restart will leave them stopped until they are started again.

---

## Harbor

Docs: https://www.harborframework.com/ ("from the makers of terminal-bench"). Install:
`uv tool install harbor`. Package source read directly at
`~/.local/share/uv/tools/harbor/lib/python3.14/site-packages/harbor/`.

### CLI flags that matter

| Need | Flag |
|---|---|
| Local task/dataset dir | `-p, --path <dir>` (a single task dir or a dir of task dirs) |
| Remote dataset | `-d, --dataset name@version`, `--repo org/name`, `-t, --task org/name` |
| **Task filtering by id** | `-i, --include-task-name <glob>` (repeatable), `-x, --exclude-task-name`, `-l, --n-tasks` |
| Agent | `-a, --agent <name>` **or a custom import path `module.path:ClassName`** |
| Model | `-m, --model <str>` (pi wants `provider/model`) |
| Agent kwargs | `--ak key=value` (repeatable) → passed to the agent's `__init__` |
| Agent env vars | `--ae KEY=VALUE` (repeatable) → `extra_env` |
| Concurrency | `-n, --n-concurrent` (default 4); `--n-concurrent-agents` caps agent phase only |
| Output dir | `-o, --jobs-dir <path>` (default `jobs/`) |
| Job name | `--job-name` (default timestamp) |
| Timeouts | `--timeout-multiplier`, `--agent-timeout-multiplier`, `--verifier-timeout-multiplier`, `--environment-build-timeout-multiplier` |
| Environment | `--env docker` (default) / `daytona` / others; `--no-delete` keeps containers; `--force-build` |
| Resources | `--override-cpus`, `--override-memory-mb`, `--cpus/--memory <auto\|limit\|request\|guarantee\|ignore>` |
| **Custom verifier** | `--verifier module.path:ClassName`, `--verifier-kwarg k=v` |
| Attempts | `-k, --n-attempts` |
| Misc | `-y` auto-confirm, `-q` quiet, `--debug`, `--env-file`, `--print-config` |

Custom import paths are resolved with `importlib.import_module` inside **harbor's own venv**, so the
module must be on `PYTHONPATH` (we export `PYTHONPATH=<repo root>`).

### Output layout

```
<jobs-dir>/<job-name>/
  result.json  config.json  lock.json  job.log
  <task_id>__<shortuuid>/          # one trial dir per task
    result.json      # TrialResult
    config.json  lock.json  trial.log  exception.txt (on failure)
    agent/           # mounted to /logs/agent in the container — agent writes logs here
    verifier/        # mounted to /logs/verifier — reward.txt / reward.json land here
    artifacts/       # manifest.json + collected paths
```

Container mounts: `/logs/agent`, `/logs/verifier`, `/logs/artifacts`; `/tests` is copied in by the
verifier **after** the agent runs; `/solution` only by the oracle agent.

### Result schema (`harbor.models.trial.result`)

- `TrialResult`: `task_name`, `trial_name`, `task_id`, `task_checksum`, `config`, `agent_info`,
  `agent_result: AgentContext|None`, `verifier_result: VerifierResult|None`, `exception_info`,
  `started_at`, `finished_at`, `environment_setup/agent_setup/agent_execution/verifier: TimingInfo`.
- `VerifierResult` has **one** field: `rewards: dict[str, float|int] | None`. **There is no
  `pass` field** — pass/fail is ours to define from the rewards dict (see ERP-Bench below).
- `AgentContext` (what our agent must populate): `n_input_tokens` (incl. cache), `n_cache_tokens`,
  `n_output_tokens`, `cost_usd`, `rollout_details`, `metadata`.
- Reward source of truth in the container: `/logs/verifier/reward.json` (preferred) else
  `reward.txt` (a bare float).

### Custom agents

Subclass `harbor.agents.base.BaseAgent` and implement:

```python
@staticmethod
def name() -> str: ...
def version(self) -> str | None: ...
async def setup(self, environment: BaseEnvironment) -> None: ...
async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None: ...
def populate_context_post_run(self, context: AgentContext) -> None: ...   # optional
```

Constructor kwargs Harbor injects: `logs_dir` (= host `<trial>/agent/`), `model_name`, `logger`,
plus `mcp_servers`, `skills_dir`, `load_trajectory` when configured, plus **anything passed with
`--ak k=v`**. (`task_dir`/`trial_paths`/`agent_timeout_sec` are injected for the *oracle* only.)

**The agent object runs on the HOST.** It drives the task container through the `environment`
handle → this settles P0.5: `harness/container.py` implements **mode (b), host-side driver**.

`BaseEnvironment` API we use:
- `await environment.exec(command, cwd=None, env=None, timeout_sec=None, user=None) -> ExecResult`
  with `ExecResult(stdout, stderr, return_code)`
- `await environment.upload_file(source_path, target_path)` / `upload_dir(source_dir, target_dir)`
- `await environment.download_file/-dir(...)`

Signalling completion = returning from `run()`. There is no in-band "done" tool; our `finish`
semantics are internal to our loop.

`BaseInstalledAgent` (used by `pi`, `claude-code`, …) instead installs a CLI **inside** the
container and shells out to it.

---

## ERP-Bench

Source: https://github.com/agentic-labs/erp-bench (also the HF dataset `agentic-labs/erp-bench`,
which returns 401 unauthenticated — the GitHub clone is the working copy). Cloned to
`vendor/erp-bench` (221 MB). The dataset is the one used in the Anchor paper, *"Preventing Artifact
Drift in Agent Benchmark Generation."*

### Task layout

```
tasks/<task_id>/
  task.toml                 # metadata + timeouts + resources
  instruction.md            # the natural-language task given to the agent
  environment/  Dockerfile entrypoint.sh odoo.conf scenario_data.json setup_scenario.py
  tests/        test.sh checks.py                 # ← DENY-LISTED for eval100 tasks
  solution/     solve.sh solver.py optimal_plan.json   # ← DENY-LISTED for eval100 tasks
```

**Guard deny paths:** `vendor/erp-bench/tasks/<eval-id>/tests/**` and
`vendor/erp-bench/tasks/<eval-id>/solution/**`.

All 300 `environment/Dockerfile`, `entrypoint.sh` and `odoo.conf` files are **byte-identical**
(md5 checked); only `scenario_data.json` differs per task. So the image is the same everything
across configs, and Docker layer caching makes per-task builds cheap after the first.

### Metadata (`task.toml`)

`[metadata]`: `scenario_number`, `name`, `difficulty` (easy/medium/hard), `category = "erp"`,
`objective_kind`, **`task_pattern`** ← the stratification key, `tags`, `seed`.
`[verifier] timeout_sec = 300`, `[agent] timeout_sec = 3600`,
`[environment] build_timeout_sec = 600, cpus = 3, memory_mb = 4096, storage_mb = 2048,
allow_internet = true`.

Counts: **300 tasks / 29 patterns**. difficulty: 50 easy, 166 medium, 84 hard.
`objective_kind`: min_new_spend 183, vendor_consolidation 34, capacity_preservation 31,
constraint_only 24, repair_plan 28.

Patterns and counts (29):

```
12  22_single_bom_split_by_capacity_invoicing        11  13_single_subassembly_qualified_workcenters
12  21_single_bom_lowest_cost_screened_mixed_seeded  11  12_single_subassembly_lowest_cost
12  20_manufacture_only_no_available_vendors         11  10_single_bom_split_by_capacity
11  25_shared_component_subassemblies_branch_...     11  09_single_bom_lowest_cost
11  24_single_subassembly_shared_overflow_...        11  08_single_bom_single_workcenter
11  23_restricted_subassembly_qualified_...          11  07_screened_buy_only_mixed_seeded_invoicing
11  19_manufacture_only_no_buy_route                 11  06_screened_buy_only_mixed_seeded
11  18_manufacture_only_policy_forbidden             10  repair_plan_medium
11  17_shared_component_subassemblies_branch_...     10  repair_plan_hard
11  14_single_subassembly_shared_overflow_capacity   10  26_buy_only_net_30_no_adjacent_data
                                                     10  16_serial_subassemblies_branch_assigned
                                                     10  15_parallel_subassemblies_branch_assigned
                                                     10  11_restricted_subassembly_qualified_workcenters
                                                     10  05_screened_buy_only_all_seeded
                                                      8  repair_plan_easy
                                                      8  04_buy_only_percentage_downpayment
                                                      8  03_buy_only_fixed_downpayment
                                                      8  02_buy_only_immediate_invoicing
                                                      8  01_buy_only_baseline
```

29 patterns × 3 = 87, so eval100 = 3 per pattern + 13 extra by round-robin (P1.1).

### Dev task (never used for eval)

**`2000_easy_01_buy_only_baseline`** — chosen 2026-08-31, before `configs/eval100.txt` exists.
`select_eval100.py` must exclude it explicitly.

### Reward semantics

`tests/test.sh` writes `/logs/verifier/reward.json` **and** `reward.txt`, plus `checks.log`,
`rule_results.tsv`, `optimality.json`, `spend.json`.

`reward.json` top-level keys:

```
overall_score  float 0..1     ← the headline reward
passed         bool           ← the benchmark's own pass flag  (pass@1 = mean of this)
constraint     {earned, total}
hygiene        {earned, total}
optimality     {score, total}
rules          {total, applicable, passed, failed, not_applicable,
                by_dimension: {constraint:[{rule,args,expr,status,passed,applicable}], ...}}
spend          {expected_spend, total_new_spend, spend_delta, spend_score, ...}
optimality_detail {objective_kind, optimality_score, primary_actual, primary_expected, primary_score}
```

**Per-rule flags are exposed** (`rules.by_dimension`), which makes failure coding cheap in P1.4/P5.3
without ever reading the rule *logic*. `reward.txt` holds the same scalar (`"0.00"`).

Measured on dev task `2000_easy_01_buy_only_baseline`:
- **nop** (untouched environment): `overall_score = 0.0`, `passed = false`,
  constraint 0/27, hygiene 1/5, optimality 100/100, rules 4/32 applicable passed.
- **oracle** (reference solution): `overall_score = 100.0`, `passed = true`,
  constraint 31/31, hygiene 6/6, optimality 100/100, rules 37/37 applicable passed.

**`overall_score` is on a 0–100 scale, not 0–1** (nop 0.0 → oracle 100.0). `reward.txt` carries the
same number formatted to 2 dp. Note the *totals* differ between runs (constraint 27 vs 31, hygiene
5 vs 6): rule applicability depends on the end state, so **never** compare `earned` counts across
tasks or runs without their `total`. Use `overall_score` for reward and `passed` for pass@1.

### Odoo / Postgres access inside the task container

One container runs **both** Odoo 19 and PostgreSQL 18 (`pg_ctlcluster 18 main`).

| Fact | Value |
|---|---|
| Odoo URL | `http://127.0.0.1:8069` |
| Database | `bench` |
| Admin login / password | `admin` / `pass` |
| API key | contents of `/etc/odoo/api_key` (generated at boot, 90-day `rpc` key for admin) |
| Odoo master password | `helloworld` (`admin_passwd` in `/etc/odoo/odoo.conf`) |
| **dbfilter** | not set in `odoo.conf` → snapshot databases (P3.2) are reachable |
| Postgres | `127.0.0.1:5432`, role `odoo` / password `odoo`, **SUPERUSER CREATEDB CREATEROLE** |
| Setup-complete marker | `/tmp/saas_setup_complete` (poll this before the agent starts) |
| Container user | `root` |
| Python | 3.12.3, `pip3` present |
| Present | `psycopg2`, `odoolib` (odoo-client-lib 2.0.0), `psql`, `curl` |
| Absent | `pandas`, `jq` |
| Outbound network | `allow_internet = true`; **`pip3 install --break-system-packages <pkg>` works** (ortools installed in 5.7 s), so P1.4's optional solver dependency needs no vendoring. PEP 668 applies here too. |
| Absent (relevant) | `git`, `jq`, `pandas`, `ortools`, `pulp` |

The instruction tells the agent to use `odoolib` with `protocol="json2"`. Whether the legacy
`/xmlrpc/2/object` endpoint still answers on Odoo 19 is recorded under *Odoo RPC* below —
`harness/lib/erp.py` follows whatever actually works, not PLAN's assumption of `xmlrpc.client`.


### Odoo RPC — measured

Both transports answer on Odoo **19.0-20260817**:

- **XML-RPC works**: `/xmlrpc/2/common` → `authenticate("bench", "admin", <api_key>, {})` → `uid = 2`;
  `/xmlrpc/2/object` → `execute_kw(...)`. PLAN's stdlib `xmlrpc.client` design is viable, and it
  keeps `harness/lib/erp.py` dependency-free. **Chosen transport.**
- **JSON-2 works**: `odoolib.get_connection(protocol="json2", port=8069, database="bench",
  login="admin", password=<api_key>)`. This is what `instruction.md` advertises, so pi (config A)
  uses it. Endpoints look like `POST /json/2/<model>/<method>`.
- Only `/xmlrpc/2/db` (database management) is deprecated — the entrypoint uses
  `POST /web/database/create` instead.

### Starting state of a task environment (dev task 2000)

- **`sale.order` count = 0.** The customer demand exists only in `instruction.md`; the agent must
  create *and confirm* the sales orders itself. `check.invariants` item 2 ("demand covered") must
  therefore work from orders the agent created, not from pre-seeded ones.
- 52 `product.product`, 113 `res.partner`. Finished product carries `default_code`
  (e.g. `P674891CF17-SPP-001`) and on-hand stock.
- Vendor capacity limits live in **`res.partner.comment` (Internal Notes)**; `product.supplierinfo`
  has **no `max_qty` field** (stated in `instruction.md` and confirmed by the schema).
- Idle container memory: **~330 MiB** (far below PLAN's 1.5 GB estimate); measure again under agent
  load before fixing N.

### Timing (warm image, dev task, this machine)

environment_setup ≈ 2 s · oracle agent_execution ≈ 40 s · verifier ≈ 8 s → ~50 s per oracle trial.
First build of the image ≈ 4 min; image is 3.42 GB and is rebuilt per task id (layer-cached).

### Environment patches (uniform, all 300 tasks — `scripts/patch_tasks.py`)

1. `uv pip install --system` → `uv pip install --system --break-system-packages`.
   Current uv honours PEP 668 on the Debian-based `odoo:19` image, so **every** task environment
   failed to build (`error: externally-managed-environment`). Applied identically to all 300 tasks.

---

## Discrepancies with PLAN.md

1. **pi repo.** PLAN says clone `earendil-works/pi`. That repo exists (the upstream Pi agent
   harness, TypeScript/npm), but Harbor's built-in `pi` agent and ERP-Bench's `agents/pi.py`
   install **`agentic-labs/pi-mono`**. The leaderboard harness is the pi-mono build.
2. **Odoo RPC.** PLAN specifies `xmlrpc.client` against `/xmlrpc/2/object`. ERP-Bench's own
   instruction and entrypoint use `odoolib` with the **JSON-2** protocol and note that
   `xmlrpc/2/db` is deprecated in Odoo 19.
3. **Verifier/oracle live in the task dir**, not a separate grader service:
   `tasks/<id>/tests/` and `tasks/<id>/solution/`.
4. **Harbor has no `pass` field**; pass@1 must come from `reward.json.passed`.
5. **Reward JSON is nested** and Harbor 0.22.0 requires flat `dict[str, float|int]`. Worked around
   with `harness/verifier.py:FlatVerifier` (`--verifier harness.verifier:FlatVerifier`), which only
   flattens the parsed dict — scoring is untouched.
6. **Task environments do not build** with current uv (PEP 668); see *Environment patches*.
7. **`--ak`/agent kwargs** are how config ids reach our agent; PLAN's "config loader" reads
   `configs/configs.yaml` keyed by that id.

---

## pi

The leaderboard harness. There are **two** installers with the same CLI, and they are not the same
thing — this matters for which one config A uses:

| | `-a pi` (Harbor built-in) | `-a agents.pi:Pi` (ERP-Bench's copy) |
|---|---|---|
| install | `npm install -g --ignore-scripts @earendil-works/pi-coding-agent@<version\|latest>` | `git clone --depth 1 agentic-labs/pi-mono` + `npm install && npm run build` + `npm i -g ./packages/coding-agent` |
| pinning | **`--ak version=0.84.4`** pins an npm release | `--ak version=<git ref>`, but pi-mono publishes **no tags** |
| measured setup time | **6.5 s** | full monorepo build (minutes per trial) |
| extra flags | `--session-dir /logs/agent/pi/sessions`, `--thinking` | `--no-session`, `--thinking`, `--max-turns` (**the CLI has no such flag**) |
| custom endpoints | yes (`model_api` kwarg + configured base URL) | no |

**Decision for config A: Harbor's built-in `-a pi` with `--ak version=<pinned>`.** Same CLI, same
four tools, same system prompt, but it pins to a published npm version instead of a moving default
branch, and it costs 6.5 s per trial instead of a monorepo build. Both are recorded here because
ERP-Bench's own README runs its copy; the harness under test is identical either way.

Vendored source for reading: `vendor/pi-mono` (root package 0.0.3, `packages/coding-agent`
**0.84.1**). The npm package installed by Harbor on 2026-08-31 resolved to **0.84.4**, which is why
the version must be pinned before P1.2 rather than left at `@latest`.

Invocation actually issued by Harbor's built-in agent (captured verbatim from `trial.log`):

```
. ~/.nvm/nvm.sh; pi --print --mode json --session-dir /logs/agent/pi/sessions \
   --provider openrouter --model z-ai/glm-5.1 '<instruction>'
```

- **Tools (4 built-ins): `read`, `bash`, `edit`, `write`.** No planning, no todo, no finish tool.
- **System prompt** — verbatim, from `packages/coding-agent/src/core/system-prompt.ts`
  (`toolsList` is only populated for tools with a one-line snippet; `guidelines` is assembled from
  the tool set, always ending with the last two bullets):

```
You are an expert coding assistant operating inside pi, a coding agent harness. You help users by reading files, executing commands, editing code, and writing new files.

Available tools:
- read: ...
- bash: ...
- edit: ...
- write: ...

In addition to the tools above, you may have access to other custom tools depending on the project.

Guidelines:
- Use bash for file operations like ls, rg, find
- Be concise in your responses
- Show file paths clearly when working with files

Pi documentation (read only when the user asks about pi itself, its SDK, extensions, themes, skills, or TUI):
- Main documentation: <readmePath>
- Additional docs: <docsPath>
- Examples: <examplesPath> (extensions, custom tools, SDK)
- When reading pi docs or examples, resolve docs/... under Additional docs and examples/... under Examples, not the current working directory
- When asked about: extensions (docs/extensions.md), examples/extensions/), themes (docs/themes.md), skills (docs/skills.md), prompt templates (docs/prompt-templates.md), TUI components (docs/tui.md), keybindings (docs/keybindings.md), SDK integrations (docs/sdk.md), custom providers (docs/custom-provider.md), adding models (docs/models.md), pi packages (docs/packages.md), environment variables (docs/environment-variables.md)
- When working on pi topics, read the docs and examples, and follow .md cross-references before implementing
- Always read pi .md files completely and follow links to related docs (e.g., tui.md for TUI API details)

Current working directory: /workspace
```

The instruction reaches the model as the **first user message** (the CLI positional arg). There is
**no ERP/Odoo-specific text in pi's prompt** — the Odoo credentials and the `odoolib` snippet come
from ERP-Bench's own `instruction.md`. That is exactly the "generic coding-agent harness" baseline.

| Setting | Value |
|---|---|
| Step / turn cap | **none.** `--max-turns` does **not exist** in pi 0.84.1 (`packages/coding-agent/src/cli/args.ts`); the only bound is Harbor's `[agent] timeout_sec = 3600`. Harbor's `Pi` agent still advertises a `max_turns` CLI flag — passing it would make pi error on an unknown argument. **Do not pass `--ak max_turns=...`.** |
| Temperature | **not set** → provider default (pi only sends `temperature` when explicitly configured) |
| max_tokens | not set → provider default |
| Bash output truncation | last **2000 lines** or **50 KB**, whichever first; full output written to a temp file whose path is shown |
| Bash timeout | none by default (per-call `timeout` arg optional) |
| Thinking | `--thinking off|minimal|low|medium|high|xhigh` (Harbor exposes it as `--ak thinking=...`) |
| Output for accounting | `--mode json` NDJSON on stdout; Harbor sums `message_end` events' `usage.{input,output,cacheRead,cacheWrite}` and `usage.cost.total` into `AgentContext` |
| Providers | anthropic, openai, google, openrouter, groq, fireworks, huggingface, mistral, xai, amazon-bedrock, github-copilot — selected by the `provider/` prefix of `-m` |

### pi trajectory format (what `ingest_harbor.py` parses)

`--mode json` writes NDJSON to `/logs/agent/pi.txt` (mounted to `<trial>/agent/pi.txt`). Event
`type`s observed on a live run: `session`, `agent_start`, `turn_start`, `message_start`,
`message_end`, `tool_execution_start`, `tool_execution_update`, `tool_execution_end`, `turn_end`.

- One **step** = one `turn_start` … `turn_end` pair.
- `message_end` carries `message.role`, `message.content` (a list; tool calls appear as
  `{"type": "toolCall", "name": ..., ...}`) and `message.usage = {input, output, cacheRead,
  cacheWrite, cost: {total}}`. Harbor's `Pi.populate_context_post_run` sums exactly these.
- pi also writes a session directory under `/logs/agent/pi/`.

Mapping to our common schema: `usage.input = input + cacheRead` (pi reports `input` **excluding**
cache, unlike OpenRouter's `prompt_tokens`), `usage.cached = cacheRead`, `usage.output = output`.

**Prompt caching is real here.** A live pi run on the dev task with GLM-5.1 reported
`cacheRead = 11,264` of `input + cacheRead = 23,162` prompt tokens within the first 7 turns, so the
P0.4 probe's `cached_tokens = 0` was only an artefact of its ~200-token prompt. Our loop's static
system prefix should get the same treatment — confirm with real numbers in P2.9.

**Reproducibility:** pin with `--ak version=0.84.4` (the version Harbor's `@latest` resolved to on
2026-08-31) and record `agent_info.version` from every trial's `result.json` in `meta.json`. Without
the pin, `@latest` moves under us mid-study and violates "same everything across configs".

### Config A on the dev task — it works, and it wins

`harbor run -p tasks/2000_easy_01_buy_only_baseline -a pi -m openrouter/z-ai/glm-5.1`:

| | |
|---|---|
| reward | **overall_score 100.0, passed** (constraint 31/31, hygiene 6/6, 37/37 applicable rules) |
| turns | 20, all of them `bash` |
| tokens | input 215,123 (of which **cacheRead 168,960**), output 11,544 |
| cost | **$0.143** |
| timing | env setup 2.6 s · agent setup 6.5 s · agent 172.9 s · verifier 7.4 s |
| pi version | 0.84.4 |

So the *easy* dev task is solved by the stock harness — as expected for `01_buy_only_baseline`, the
simplest of the 29 patterns. Budget signal for Phase 4: ~$0.15 and ~3 min per easy trial; harder
patterns will cost more, so plan on roughly **$0.3–0.5 and 5–10 min per trial**, i.e. **$250–650**
for the 800-trial minimum viable set.

## Models

Both models are served through **OpenRouter** (`https://openrouter.ai/api/v1`), so config A
(pi, which has a native `openrouter` provider) and configs B/C (our OpenAI-compatible loop) hit the
same endpoint and the same weights — the only difference between them is the harness.

| | big | small |
|---|---|---|
| model | `z-ai/glm-5.1` | `qwen/qwen3-32b` |
| pi `-m` | `openrouter/z-ai/glm-5.1` | `openrouter/qwen/qwen3-32b` |
| price in / out (USD per Mtok) | 0.966 / 3.036 | 0.08 / 0.28 |
| context | 204,800 | 131,072 |
| published ERP-Bench pass@1 | **35.8** (coding mode) | — |
| observed serving provider | StreamLake | DeepInfra |
| 2-turn tool round trip | **works** | **works** |
| turn latency (trivial prompt) | 4–5 s | 8–11 s |

Key lives in `.env` (mode 600, gitignored) as `OPENROUTER_API_KEY`, mirrored to
`MODEL_BIG_API_KEY` / `MODEL_SMALL_API_KEY`. Never logged: strip `Authorization` before writing
any request to a trajectory.

### `usage` object (measured, identical shape for both models)

```json
{"prompt_tokens": 201, "completion_tokens": 39, "total_tokens": 240,
 "cost": 0.00031257, "is_byok": false,
 "prompt_tokens_details":     {"cached_tokens": 0, "cache_write_tokens": 0, "audio_tokens": 0, "video_tokens": 0},
 "completion_tokens_details": {"reasoning_tokens": 22, "image_tokens": 0, "audio_tokens": 0},
 "cost_details": {"upstream_inference_cost": ..., "upstream_inference_prompt_cost": ...,
                  "upstream_inference_completions_cost": ...}}
```

Mapping to our common schema: `usage.input = prompt_tokens` (this **includes** cached tokens),
`usage.cached = prompt_tokens_details.cached_tokens`, `usage.output = completion_tokens`.

Three things this buys us that PLAN did not assume:

1. **`cost` is returned per call in USD.** Cost per task is therefore measured, not derived from a
   price table — `pricing_per_mtok` in `configs/models.yaml` is kept only as a cross-check.
2. **`completion_tokens_details.reasoning_tokens`** is reported separately (GLM-5.1 spent 22 of 39
   output tokens reasoning; Qwen3-32B spent 195 of 218). Log it: an ablation that changes output
   token count may be changing reasoning length, not answer length.
3. **`provider` is reported per response** (which upstream actually served the request). Log it per
   call — OpenRouter routes across providers, and a quantisation difference between providers would
   silently violate "same model versions across configs". If drift shows up, pin with
   `"provider": {"order": [...], "allow_fallbacks": false}`.

### Account restriction: only GLM-5.1 is reachable (2026-09-01)

After the account-level provider allowlist was set (to control GLM-5.1's cost and
quantization), **every model except `z-ai/glm-5.1` returns HTTP 404**:

```
No endpoints available matching your guardrail restrictions and data policy.
```

Tested and blocked: qwen3-32b, qwen3-30b-a3b-instruct-2507, qwen3-235b-a22b-2507,
mistral-small-3.2-24b, gemma-3-27b, llama-3.3-70b, phi-4, hermes-4-70b, deepseek-v3.2-exp,
glm-4.5-air, glm-4.6, glm-4.7, glm-4.7-flash, glm-5, glm-5.2, glm-5.3-flash.

This is **not** the provider allowlist, and the diagnosis is worth keeping:

* After DeepInfra was allowed, GLM-5.1 routed through it successfully — so DeepInfra as a
  provider is permitted.
* `qwen/qwen3-32b` is served by `deepinfra/fp8`, yet is still refused, with
  `provider: {only: ["deepinfra"], data_collection: "allow"}` explicitly set.
* Same provider, different model, opposite result → the gate is per-model/endpoint, i.e.
  the "guardrail restrictions" or data-policy half of the message, not the provider list.
* `GET /api/v1/key` shows no key-level restriction (`limit: null`, no model allowlist).

**Consequence for the study:** the small model (P0.4's `qwen/qwen3-32b`) cannot run until
this is lifted. The main result — our harness against pi on the *same* model — needs only
`big`, so Phases 2-4 proceed on GLM-5.1 and the small-model column is added later if the
account is opened.

**Also measured:** with DeepInfra allowed, GLM-5.1 routes to it about a third of the time
(6 probes: 3 GMICloud, 2 DeepInfra, 1 Chutes), and DeepInfra serves GLM-5.1 at **fp4**
while GMICloud and Chutes serve fp8. That is the "same model across configs" risk in
PLAN.md hard rule 9 becoming real. Our loop pins `quantizations: [fp8]` per request; pi
(config A) cannot, so if the allowlist keeps DeepInfra, config A's trials are a mix of fp8
and fp4. Preferred fix: allow **gmicloud + chutes + nebius** instead — nebius serves
qwen3-32b and serves GLM-5.1 at fp8, so no fp4 endpoint is reachable at all.

### Provider routing (measured 2026-09-01)

OpenRouter serves `z-ai/glm-5.1` from 14 upstreams that differ in **price** and in
**quantization**:

| upstream | in $/Mtok | out $/Mtok | **cache read $/Mtok** | quant |
|---|---|---|---|---|
| gmicloud | 0.910 | 2.860 | 0.169 | fp8 |
| streamlake | 0.966 | 3.036 | 0.179 | fp8 |
| chutes | 0.980 | 3.080 | **0.098** | fp8 |
| deepinfra | 1.050 | 3.500 | 0.205 | **fp4** |
| siliconflow | 1.190 | 3.740 | **0.600** | fp8 |
| nebius | 1.400 | 4.400 | 0.000 (no discount) | fp8 |

Cache reads are ~80% of our input tokens, so the 6x spread on that column ($0.098-$0.600)
dominates cost — a trial costs ~$0.49 on chutes and ~$1.79 on a provider without cache
discounting. And uncontrolled routing is a **validity** problem, not only a cost one:
fp4 and fp8 are different weights, so different configs could be measured against
different models, breaking PLAN.md hard rule 9.

**Restricted account-wide to gmicloud + chutes (both fp8).** Verified by probe: 8
consecutive calls served 7x GMICloud, 1x Chutes, no other upstream. This is set in the
OpenRouter dashboard rather than per request because **pi offers no routing hook**, and
config A has to be covered too. `configs/models.yaml` additionally carries
`provider_routing` for our own loop to send as the `provider` field.

Provider slugs come from `GET /api/v1/models/{id}/endpoints` (`tag` field, e.g.
`gmicloud/fp8`).

**Caching is explicit, not implicit.** Every endpoint reports
`supports_implicit_caching: false`, yet a live pi run showed 80% of input tokens billed as
cache reads — so the caching comes from explicit cache breakpoints in the request, not from
the provider caching on its own. `harness/llm.py` must therefore set cache breakpoints
itself; a static system prefix alone will not be cached.

**Prompt caching: unverified.** `cached_tokens` was 0 on both models, but the probe prompt was only
~200 tokens and providers typically require ≥1k tokens before caching engages. Re-measure with the
real config-C system prompt in P2.9 before claiming any cached-token savings; the
`supports_prompt_cache` field in `configs/models.yaml` stays `null` until then.

Tool-call format: standard OpenAI `tool_calls` with `function.name` / `function.arguments`
(a JSON string). Call ids differ in shape by provider (`call_eb3c8c36…` vs `call_oNXesMEH…`) —
echo them back verbatim, never construct them.

Rate limits: OpenRouter returned **no** `x-ratelimit-*` headers on these calls. Treat 429s as the
only signal and rely on the loop's exponential backoff (max 5 retries → `api_error`).

## Minting

**Verified working.** The Anchor generator ships in the repo and mints fresh instances in
milliseconds (CP-SAT solve ≈ 0.01 s per scenario).

```bash
cd vendor/erp-bench && uv sync            # ortools, pydantic, jinja2, numpy
uv run generate-tasks --category procurement \
  --difficulty easy --count N --seed 1 --start-number 9000 \
  --output ../../data/minted --force
# other flags: --pattern, --difficulty-mix, --dataset-config, --list-topology-combinations,
#              --print-difficulty-presets, --num-search-workers, --metrics-output
```

Round-tripped end to end: `harbor run -p data/minted/9000_easy -a oracle` →
`overall_score = 100.0`, `passed = true`, constraint 22/22, 54 s total.

Two caveats that change how PLAN.md's P1.1 should use them:

1. **Minted `task.toml` has no `task_pattern`** (and uses `version = "1.0"` instead of
   `schema_version = "1.1"`). Stratifying a dev set by pattern therefore needs either the
   `--pattern` flag at mint time (one mint run per pattern) or the shipped tasks.
2. Minted environments need the same PEP 668 patch: run
   `python3 scripts/patch_tasks.py --tasks-dir data/minted` after every mint.

**Decision:** build `dev40/dev10/dev5` from the 200 shipped non-eval tasks (they carry
`task_pattern`, so the dev curve is stratified the same way eval100 is). Keep minting for extra
capacity when a specific pattern needs more dev instances than dev.txt provides — those instances
are, by construction, not eval tasks.

## Reproduction

### The published numbers and where they come from

Not in the erp-bench repo and not on Harbor Hub (no ERP-Bench leaderboard exists there). The source
is the **Anchor paper, arXiv:2605.26321, Appendix G.1, Table 12** — *"Anchor: Mitigating Artifact
Drift in Agent Benchmark Generation"*.

| Model | coding pass@1 | coding pass@5 | browser pass@1 | computer-use pass@1 |
|---|---|---|---|---|
| GPT-5.5 | **43.4** | 73.0 | 9.7 | 7.9 |
| GLM-5.1 | **35.8** | 63.3 | 2.4 | — |
| Claude Opus 4.7 | **30.8** | 60.7 | 28.8 | 23.7 |
| Kimi K2.5 | **9.1** | 20.3 | 1.5 | — |

Their setup, in their words: the "minimal, open-source **pi-mono** agent toolkit", coding harness =
"shell and filesystem tools, driving Odoo through the **JSON-2 API**"; toolkit surface "read, write,
edit, bash, grep, find, and ls"; each trial gets a **one-hour timeout**, a **400-turn budget**, and
"provider-default reasoning effort where exposed"; **five trials per agent-task pair** over all 300
tasks (1,500 trials per model-harness pair). A pass is "the agent's terminal state satisfies all
applicable constraint checks and achieves the task objective".

### How our config A differs, and why the ±8-point tolerance is the right test

| | Anchor Table 12 | our config A |
|---|---|---|
| tasks | all 300 | frozen 100 (stratified over all 29 patterns) |
| trials per task | 5 | 1 |
| harness | pi-mono toolkit | pi 0.84.4 (`@earendil-works/pi-coding-agent`), same CLI and tools |
| turn budget | 400 | none enforced (pi 0.84.4 has no `--max-turns`); bounded by the 1 h timeout |
| timeout | 1 h | 1 h (`[agent] timeout_sec = 3600` in every task.toml) |
| tools | "read, write, edit, bash, grep, find, ls" | pi's four defaults: read, bash, edit, write |

pi 0.84 defines all seven (`allToolNames` in `packages/coding-agent/src/core/tools/index.ts`) but
activates only four unless `--tools` is passed, and **ERP-Bench's own `agents/pi.py` does not pass
`--tools`** — so the four-tool default is what their runs used too, and the paper's list describes
the toolkit surface rather than the active set. For these tasks the difference is small: grep/find/ls
are filesystem tools, and the work happens through Python scripts against the JSON-2 API.
| temperature | provider default | provider default (pi sends none) |

Sampling noise alone puts a 1-trial, 100-task estimate of a ~36% rate at about **±4.8 points (1 SE)**,
so PLAN's ±8-point band is roughly ±1.7 SE — a real check, but it cannot resolve small harness
differences. Two of the differences above (4 tools vs 7, no turn cap) are worth re-checking if the
observed number lands outside the band before blaming the environment.

### Cost and time structure of config A (49 eval100 trials)

| | per trial |
|---|---|
| input tokens | **1,247,763** (median 1.19M, p90 2.37M, max 5.12M) |
| of which cache reads | 1,000,394 — **80%** |
| output tokens | 46,618 |
| cost | **$0.730** (uncached input $0.24, cache reads ~$0.35, output $0.14) |
| steps | median 27, p90 45, max 70 |
| agent wall-clock | median 12 min, p90 32 min, max 48 min |

| | passed (22) | failed (27) |
|---|---|---|
| mean cost | $0.46 | **$0.95** |
| mean steps | 22 | 34 |
| mean wall-clock | 10 min | 20 min |

**72% of the spend went to trials that failed.** The cost driver is context volume, not
answer length: pi re-sends a conversation that grows with every unpaged tool dump (its bash
tool alone permits 50 KB per call) across a median of 27 steps. This is the quantitative
case for the paging layer (P2.2), the typed client (P2.3) and the finish gate (P2.4) —
they attack exactly the two things the data blames.

Decisions taken from this (2026-09-01):
- **Iteration runs default to the small model** (`Makefile MODEL ?= small`; `run.py` warns
  when `big` is asked for on dev5/dev10). A dev trial is ~$0.10 there against ~$0.73.
  Only dev40 curve points, which go into the write-up, use big.
- **`token_cap` lowered 2.5M -> 1.5M** for our configs: above the median, below p90, so it
  bounds the tail without touching a typical trajectory. Config A stays uncapped, since pi
  has no such cap and adding one would break comparability.
- **N raised to 12** after the Docker VM went from 7.75 GiB to 31.3 GiB.

### Attempt 1 (2026-09-01) — invalid, out of credit

`runs/INVALID__A_pi__big__eval100__20260901T063446Z`. The OpenRouter account held $10 and was
exhausted about a quarter of the way in. **66 of 100 trials returned HTTP 402 before their first
model call**; 76 recorded exactly one step and $0 of spend. The aggregate read pass@1 3.8%,
mean reward 4.6 — entirely a billing artefact.

Two lessons, both now enforced in code:

1. **A funding failure looks like a bad model.** Trials complete "successfully" with
   `terminal_reason: finish` because pi exits cleanly after its retries are refused. Nothing in
   Harbor's result signals the outage. `scripts/run.py` now runs a balance preflight and refuses
   to launch; `aggregate.py` skips any run whose `meta.json` sets `invalid`.
2. **OpenRouter reserves credit against the requested `max_tokens`, not the tokens used.** pi asks
   for ~180k tokens per call, so requests are refused while the nominal balance is still positive
   (the error text: "You requested up to 179869 tokens, but can only afford 130715"). The preflight
   therefore demands the estimate plus $5 of floor.

Measured cost per config-A trial: **$0.23** on the five easiest dev tasks, **~$0.33** across the
eval100 mix before the money ran out. `est_cost_per_trial_usd` in `configs/models.yaml` is set
from these.

### Result (2026-09-01)

| config | model | n | pass@1 | published | Δ | mean reward | steps | $/task |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A_pi | z-ai/glm-5.1 | 100 | **39.0** | 35.8 | **+3.2** | 54.7 | 27.7 | 0.624 |
| A_pi | qwen/qwen3-32b | 67 | 0.0 | — | — | 1.2 | 15.0 | 0.007 |

**Reproduction holds**: 39.0 against 35.8, a 3.2-point gap inside the ±8-point tolerance and
well within the ±4.8 points (1 SE) that a 1-trial, 100-task estimate carries by construction.
Full write-up in `analysis/reproduction.md`.

Two caveats recorded rather than smoothed over:

* Four trials were **interrupted** by a run being stopped by hand (`2067`, `2074`, `2149`,
  `2174`), so their verifier scored whatever state existed at the kill. That biases the
  figure down; excluding them gives 40.6 over 96 trials. Re-running those four cleanly
  (~$3) would remove the caveat.
* The `qwen/qwen3-32b` run was **stopped at n = 67** once GLM-5.1 finished. It had passed
  0 tasks at a mean reward of 1.2, so the remaining trials would have refined a floor
  result at ~4 trials/hour. The figure is reported with its real n, not as 100.

Coverage is complete: 100 unique eval100 task ids across three fp8-only batches
(49 + 34 + 17), no duplicates, none from outside the frozen set.

## Odoo wizards and other Odoo-19 traps

Everything here was hit while making `tests/test_erp.py` pass against the live image, and
every one of them is a way to get a **silently wrong end state** rather than an error.

### 1. Marking an MO done consumes nothing unless you write the raw moves

`action_assign` + `qty_producing` + `button_mark_done` leaves the order at `state = done`
while its `move_raw_ids` end at `state = cancel, quantity = 0.0, picked = False`. The
finished product is created; the components are never consumed. Odoo fills a raw move's
`quantity` from an onchange that does not fire over RPC.

Fix (in `erp.produce`): after setting `qty_producing`, write
`{"quantity": product_uom_qty * share, "picked": True}` on every raw move that is not
already done or cancelled, then `button_mark_done`. Reading `mrp.production` afterwards
never reveals the problem — only `stock.move` does.

### 2. Pickings need `quantity` + `picked`, and there is no `quantity_done`

Odoo 19 renamed the done quantity to `stock.move.quantity` with a companion `picked`
boolean; `quantity_done` does not exist (`fields_get` confirms). `stock.picking` exposes
`move_ids`, not `move_ids_without_package`. A picking validated without writing those is a
zero-quantity transfer that still reports `state = done`.

### 3. `create_invoices` commits, then fails to serialise its reply

`sale.advance.payment.inv.create_invoices` returns a UI action containing nulls, and
Odoo's XML-RPC layer raises `TypeError: cannot marshal None unless allow_none is enabled`
while writing the response — **after** the transaction has committed. Verified: the
invoice exists afterwards (draft, `invoice_origin = S00001`).

`erp.invoice` therefore swallows exactly that one error and then proves the effect by
diffing `sale.order.invoice_ids` before and after, raising if no invoice appeared. Client-
side `allow_none=True` does not help — the failure is in the server's response writer.

### 4. Vendor offers hang off the template, not the variant

`product.supplierinfo.product_id` is usually `False`; the offer is attached to
`product_tmpl_id`. Reading `product_id` naively yields `None`, which then poisons every
downstream domain (`('product_id', 'in', [None])` → the marshalling error again).
`erp.suppliers` resolves the template to a variant.

### 5. `allow_none=True` on both proxies

Odoo accepts nulls happily; stdlib `xmlrpc.client` refuses to send them by default.

### Wizards actually observed

On this dataset, `button_validate` and `button_mark_done` returned **no** wizard action
once quantities were written correctly — the wizards exist for the cases where quantities
are missing or partial. `erp._run_wizard` handles the ones Odoo can raise here:

| wizard | method used | meaning |
|---|---|---|
| `stock.backorder.confirmation` | `process_cancel_backorder` | validate without a backorder |
| `stock.immediate.transfer` | `process` | confirm the transfer as proposed |
| `mrp.consumption.warning` | `action_confirm` | accept the consumption difference |
| `mrp.immediate.production` | `process` | produce as proposed |

An unknown wizard raises an `OdooError` naming the model rather than guessing a method.

## Dev curve decisions

### P1.4 (2026-09-02) — what the stock harness actually fails at

Config A on dev40: **42.5% pass@1**, mean reward 55.3, 29.2 steps, $0.656/task, all 40
terminating cleanly. 23 failures coded from the verifier's per-rule flags plus our own
trajectories (`analysis/failures_stock_big.md`).

| code | n | share |
|---|---:|---:|
| TIMELINE | **17** | 74% |
| PREMATURE_FINISH | 2 | 9% |
| SUBOPTIMAL | 2 | 9% |
| MISSING_DOC | 1 | 4% |
| OVERSPEND | 1 | 4% |
| SUPPLIER / DRAFT / BOM_MATH / API_ERR / TOOL_LOOP / STEP_CAP / SCOPE | 0 | — |

**Zero Odoo-mechanics failures.** The stock harness can drive Odoo; what it cannot do is
check that a plan's dates work before committing to them. `supply_timing_feasible` (18
tasks), `mo_schedule_compliance` (13), `po_delivery_schedule_compliance` (11) and
`mo_component_feasibility` (11) are the rule families involved.

Two decisions PLAN.md ties to this measurement:

* **No cost-planning section, no preinstalled solver.** SUBOPTIMAL is 8.7%, against PLAN's
  40% trigger. Counter-intuitive, because 183 of the 300 tasks carry a `min_new_spend`
  objective — but only two dev failures ever produced a valid end state whose sole fault
  was cost. The other 21 never reached a feasible plan to optimise. The playbook's planning
  text is retargeted at *feasibility* (earliest completion date first, then landed cost).
* **Phase 3 order is re-ranked** in `analysis/build_order.md`: invariants 5+6 (timeline,
  BOM arithmetic) first, then state snapshots + dry run, then the briefing. `db.py` and
  `delegate` address no observed failure and move last.

## Why v0 lost, and what changed (2026-09-02)

The first valid-ish C_full dev40 trajectories (27 not killed by the in-flight 402s) paired
against config A on the same tasks: **33% vs 41% pass, 49.2 vs 54.7 reward, 50.5 vs 29.2
steps, $0.84 vs $0.66**. 11 passes to 9 — a two-task gap, but the wrong direction on every
axis. Every cause below was found in those trajectories and fixed without another paid run.

| # | defect | evidence | fix |
|---|---|---|---|
| 1 | **Cache breakpoint never moved** | C_full cached tokens frozen at exactly 3,968 (the system block) while context grew to 17,648: hit rate 76%→22%. pi: 91–98%, because its marker sits on the *last* message | `llm.apply_cache_control` copies pi-mono's `applyAnthropicCacheControl`: system prompt, last tool def, last conversation message, on a per-request copy. Re-measured live: **~72%** mean hit rate |
| 2 | **Finish gate never refused** | 19 finish calls, **0 refusals**. v0's invariants covered drafts/stock/supplier/invoicing — things P1.4 coded zero failures for — and not the 74% TIMELINE mode | Appendix B items 2, 5, 6: `demand_covered`, `timeline_feasible`, `mo_feasible`. All 14 shared failures have rules these map to. Refusals now say *how to fix* |
| 3 | **Contract named tools that did not exist** | 31 `NameError`s (`state` ×20, `db` ×11); contract step 3 impossible | `lib/state.py` (TEMPLATE snapshots, RPC-reachable, isolated) and `lib/db.py` (Postgres-enforced read-only) built; `agent.py` appends the modules actually loaded |
| 4 | **token_cap truncated work** | bound on 9/40 while the uncapped baseline averaged 1.20M | 1.5M → 3M, above config A's eval100 p90 |
| 5 | **Kernel transport dropped requests** | `docker compose cp failed` ×39, 34 in one trial; a **reward-98.0 trial lost its finish** to it and burned 20 more steps | requests travel base64 inline in the command line, 3 attempts; `finish` retried by the harness |
| 6 | **Kernel timeout destroyed state mid-plan** | `plan_fn(erp)` for five customers over XML-RPC exceeded 120s; namespace rebuilt; model redid everything step by step | 120s → 600s |
| 7 | **39 read calls per trial before writing** | exploration the model repeats every task | `lib/brief.py`: products, vendor offers with lead times and notes, BOMs, open orders — ~1.9k tokens in the first user message |
| 8 | **Gate would refuse over inherited conditions** | found by the end-to-end test on a dirty container; seeded scenarios ship with orders | `finish.record_baseline()` at task start: pre-existing failures are reported, never block |
| 9 | **`lib: []` collapsed to "ship everything"**; stale modules survived reinstall | B_bash smoke ran the full library | empty list respected; install clears the directory |

What is verified: 1 (live probe), 2 (live tests seed each violation; one drives the real
`ErpAgent` path — seeded late PO → `finish refused` naming both dates and a fix → date
moved → accepted), 3–9 (tests). What is **not** verified and decides the result: whether the
model acts usefully on a refusal. The pre-registered $4 dev5 checkpoint tests exactly that:
cache ≥ 60%, ≥1 refusal subsequently resolved, 0 NameErrors, 0 api_errors.

## Checkpoint pass 1 (2026-09-02) — what the model does with a working gate

Final: **3/5 pass, mean reward 67.2, 27.2 steps, $0.35/task** — parity with config A on the
same five tasks (3/5, 68.8, 17.8 steps, $0.23) *before* any of the fixes below were in.
Two fails: `2017` (late receipt the old check missed) and `2024` (killed at step 25 by a
504 delivered inside a 200 body, which only HTTP-status retries covered — fixed).

After pass 1, three write-time guards were added so the losing move cannot be written at
all: `create_po` refuses a `date_planned` earlier than order date + lead time; `receive`
refuses goods not yet due; `create_mo` refuses a `date_start` before components can be on
hand. Each names the date that works and takes `force=True` as the escape hatch. The
finish baseline now labels pre-existing failures instead of exempting them — 28 of 300
tasks are repair plans, where fixing what was already broken is the job.

dev5, N=3, three trials scored before the run finished: 2 passes at 100.0, one fail at
22.8 (`2017`). Against the pre-registered bar: NameErrors 0 (pass), api_errors 0 (pass),
**cache 39% (fail)**, **refusals 0 (fail)**. Each failure traced and fixed:

* **Chutes barely honours the prompt cache.** 8-turn probe: Chutes 6%, GMICloud 46% (61%
  after two cold-start writes). All 98 calls in this run routed to Chutes. pi lands on
  GMICloud 9 times in 10; that is a large part of its 83%. Now `only: [gmicloud]`.
* **`timeline_feasible` trusted the date the agent wrote.** `2017` wrote `date_planned`
  "Sep 9, 7 days from Sep 2" for a 19-day vendor, then received the goods on day 0; the
  check skipped received lines as "already arrived". Arrival is now
  `max(date_planned, order date + supplierinfo.delay)`, received or not.
* **The playbook coached the losing move.** Its example said `confirm_po` then
  `receive(po)`. Of 17 passing stock-harness dev40 trials, **14 left POs confirmed and never
  validated the receipt**. Playbook and contract now say: confirm, set a date the vendor
  can meet, do not receive; deliver only from stock on hand.
* **The arithmetic is now a function call.** `erp.feasible_vendors(product, qty, need_by)`
  returns only vendors who can land it in time, with the arrival date to use as
  `date_planned`; `erp.earliest_build(product, qty)` says when an MO can start and what to
  buy. `state.rehearse(plan_fn)` does snapshot → run → `check.all()` → drop in one call.
  Refusal messages point at these.

## Checkpoint pass 2 (2026-09-02) — routing, timeline and helpers in; force-hole found

**3/5 pass, mean reward 68.7, $0.49/task, cache 76–80% on every trial** (bar 60%; pass 1 was
29%). 2017 (pass-1 wishful-date failure) now 100; 2024 (down-payment pattern, killed by a
body-level 504 in pass 1) now 100 with `erp.invoice(so, "percentage", …)`; 2035 100.

Both failures — 2004 (21.0) and 2014 (22.7) — are one hole: the write-time guard refused an
early `receive()`, the model wrote "try force=True and see what the checks say", and the
checks said nothing. Its reasoning: a PO arriving 9/9 before a 9/11 due date is "genuinely
due" — *will arrive in time* conflated with *has arrived*. These trials uploaded their
library before the fix landed, so pass 2 cannot show it.

Fix: `no_fabricated_receipts` (hard) — a done incoming picking dated before order date +
the vendor's lead time is refused at finish; the guard message now says a PO arriving
before the due date already covers that demand and the delivery should stay pending.
Pass 3 tests it; early signal: 2035 finished at 100 after the check surfaced in three
rehearsals, and 2004 has not touched `force` at all.

## Checkpoint pass 3 (2026-09-02) — the force hole is closed; the real loss is origin and MOs

2035 and 2024 at 100. 2004: the model tried `receive(po, force=True)` **inside a
rehearsal** at step 15, `no_fabricated_receipts` failed it on the spot, and it never
touched `force` again — redesigned the plan, clean rehearsal at 29, all 20 harness checks
green on main, finish at 34. And the grader gave it **20.97**, failing exactly
`supply_timing_feasible` + `po_delivery_schedule_compliance`.

Why (read from the dev task's grader, 2004 — a dev task): a PO's origin tokens are the
demand it feeds, and **every** token must name an order whose need date is on or after
the PO's planned date. The model wrote `origin="S00001, S00002, S00003, S00004"` — two of
them due 09-07 for a PO landing 09-08. pi wrote `origin="S00004"` and passed. My checks
had no view of origin semantics at all.

Then the bigger one. On dev40's 31 make/repair tasks A passed 9 and C v0 passed **1**;
the failing rules are all MO scheduling semantics: `mo_schedule_compliance` (an MO needs
`date_deadline` — Odoo leaves it EMPTY — with deadline ≥ start + BOM `produce_delay`,
origin = the SOs it builds for, a work order on a qualifying centre),
`po_delivery_schedule_compliance` for component POs (origin = MO refs, land by the MO's
start), `mo_component_feasibility` / `component_stock_capacity_compliance` (a start-order
simulation: stock + POs at their planned date, MOs consuming in sequence),
`assembly_capacity_compliance` (qty × minutes/unit ≤ the minute limit in the centre's
Internal Notes). Every one of these is stated in the task instruction ("set the start
date and the due date", "assign workcenter on each work order", the origin conventions,
"check each workcenter's Internal Notes for the minute limit"), so encoding them as
helpers and checks is fair game. `create_mo` used to write none of it.

Built (verified against a second devbox on 2065's real make scenario, `ERP_DEV_TASK=2065…
ERP_DEV_CONTAINER=erpdev65 ERP_DEV_PORT=18070`):

- `origin_consistent` (hard): every PO/MO origin token names a real SO (need =
  commitment_date) or MO (need = date_start), and the need is on or after the document's
  honest arrival (PO: max(date_planned, order + lead); MO: date_deadline). Empty origin is
  judged only on documents created this session — seeded orders never carry one (22 POs,
  9 MOs, 145 SOs across dev40) and are not ours to edit.
- Write-time guard for the same rule in `create_po` and `create_mo` (refusal names the
  references that do qualify — a string edit, not a re-plan).
- `create_mo(..., date_deadline, origin, workcenter_id)`: deadline defaults to start +
  `produce_delay`; refuses a deadline before that; moves the auto-created work order to
  the chosen centre. `bom_for`, `workcenter_options` (min/unit, cost/unit, minutes free,
  units that fit — the $28/unit it computes on 2065 equals the grader's option cost),
  `workcenters()` now parses the minute limit from the note and sums minutes committed.
- `mo_schedule` (hard), `workcenter_capacity` (hard), `mo_feasible` rewritten as the
  start-order simulation, `so_has_commitment_date` promoted to hard (the grader returns
  None for the whole timing family when any confirmed SO lacks one).
- `earliest_build` now returns lead_days, date_deadline and the work-centre options.
- Playbook: manufacturing section rewritten around the four things an MO must carry.

### Pass 3, the other two losses (same day)

- **2017 (0.0, 65 steps, "finish")**: clean rehearsal at step 61, then
  `finish.record_baseline()` (the docs listed it under `finish`; the bound name is the
  function) → AttributeError → it `cat`ed the finish module over bash → the sentinel
  literal came back in the bash output → the loop declared the episode finished. Nothing
  was ever executed on main, and every check was vacuously green. Three fixes: only the
  finish tool (or a python call the kernel confirms as finished) ends the episode and the
  sentinel literal is split in source; the docs list only bound names and `finish.reset`
  / `finish.record_baseline` exist as attributes; the gate refuses once when
  `erp.write_log` on main is empty ("a rehearsal on a clone does not count").
- **2014 (api_error, step 30)** and make6's 2065 (step 12): five `ConnectionResetError`s
  inside ~30 s of backoff. A pinned probe showed 2 of 3 short completions at ~5 s and one
  held for >10 min — the provider drops held connections. Budget is now 8 attempts / 90 s
  cap (~5 min); make6 was stopped at ~$1.5 (quarantined) to rerun all six trials on the
  same code once the path is stable (probe monitor: 6 consecutive quick completions).
- Also fixed from make6's first steps: `import check` (ModuleNotFoundError) — library
  modules are now aliased in `sys.modules`.

## Freeze

*(P3.7)*
