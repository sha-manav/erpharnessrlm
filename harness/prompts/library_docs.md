# Preloaded library

Every name below is already bound in your Python kernel — do not import anything to
use it, and do not reimplement it. Generated from the source, so it cannot describe a
function that does not exist.

## `erp`

A typed Odoo client for procurement and manufacturing work (PLAN.md P2.3).

**class `OdooError`** — An Odoo-side failure, carrying Odoo's own message rather than our traceback.

**class `Erp`** — One authenticated connection to one database.
- `uid()`
- `models()`
- `on(db)` — A client for another database on the same server (used by `state`).
- `call(model, method, args=(), kwargs=None)` — Raw `execute_kw`. Every non-read call is appended to `write_log`.
- `search_read(model, domain, fields, limit=80, order=None)`
- `get(model, ids, fields=None)`
- `fields(model, like=None)` — What fields a model really has — the antidote to guessing names.
- `count(model, domain=None)`
- `sales_orders(state=None, due_before=None, ids=None)`
- `so_lines(so_ids)`
- `stock(product_ids=None)` — On-hand / reserved / free per product, summed over internal locations only.
- `boms(product_ids=None)`
- `suppliers(product_ids=None)` — Vendor offers from `product.supplierinfo`.
- `purchase_orders(state=None, ids=None)`
- `po_lines(po_ids)`
- `productions(state=None, ids=None)`
- `pickings(picking_type=None, state=None, origin=None)`
- `invoices(move_type='out_invoice', state=None)`
- `create_po(vendor_id, lines, date_planned=None, origin=None)` — Create a purchase order. `lines` = [(product_id, qty[, price_unit[, date_planned]])].
- `vendor_price(vendor_id, product_id, qty)` — The supplierinfo price for this vendor/product at this quantity tier.
- `confirm_po(po_id)` — `purchase.order.button_confirm`.
- `receive(po_id)` — Validate every incoming picking of a PO. Returns the picking ids validated.
- `create_mo(product_id, qty, bom_id=None, date_start=None)` — `mrp.production.create`. Odoo picks the BOM if one is not named.
- `confirm_mo(mo_id)` — `mrp.production.action_confirm`.
- `produce(mo_id, qty=None)` — Reserve, set `qty_producing`, consume the components, and mark done.
- `deliver(so_id)` — Validate every outgoing picking of a sales order.
- `invoice(so_id)` — Create the customer invoice(s) for a delivered sales order.
- `post(invoice_ids)` — `account.move.action_post`.
- `cancel(model, ids)` — Cancel records with whichever cancel method the model exposes.

## `check`

End-state invariants, from ERP practice — never from benchmark grader code.

**class `Check`**

**class `Rule`** — A task-specific rule the agent writes from the instruction.

- `invariants(client=None)` — Run the generic end-state invariants.
- `rules(client=None, rule_list=None)` — Run the agent's task-specific rules.
- `register(rule)` — Keep a rule for every later `check.all()`; re-registering an id replaces it.
- `registered()`
- `clear()`
- `all(client=None)` — Invariants plus every registered rule, in one table.
- `failures(client=None, hard_only=True)` — The rows that failed — what `finish` gates on.

## `plan`

A task ledger the agent maintains and the loop re-injects (PLAN.md P2.5).

**class `Plan`** — An ordered list of steps with a status each. Ids are 1-based and stable.
- `set(texts)` — Replace the whole plan. Use once, at the start.
- `add(text)` — Append one step and return its id.
- `update(item_id, status, note='')`
- `show()`
- `summary()` — At most `SUMMARY_LINES` lines, for re-injection into the conversation.
- `open_items()`
- `is_complete()`
- `__str__()`

## `finish`

The finish tool, and the gate in front of it (PLAN.md P2.4).

- `reset()` — Start a fresh episode (used by tests and by the agent at task start).
- `set_baseline(failing_ids)` — Record checks that were already failing before the agent acted.
- `record_baseline(client=None)` — Run the invariants now and remember which already fail. Called at task start.
- `refusals()`
- `finish(summary='', client=None, gate=True)` — End the episode, if the hard checks agree.

## `db`

Read-only SQL against the task's Odoo database (PLAN.md P3.1).

**class `SqlRefused`** — A statement that is not a read.

**class `Db`**
- `sql(query, limit=DEFAULT_LIMIT)` — Run a read-only query and return a `Table`.
- `tables(like=None)` — What tables exist — the SQL counterpart to `erp.fields`.
- `columns(table)`

## `state`

Database snapshots and the dry-run protocol (PLAN.md P3.2).

**class `State`**
- `snapshot(name)` — Clone the working database under `name`, replacing any existing clone.
- `drop(name)`
- `list()`
- `diff(a='start', b=None)` — What changed between two databases, per model.

## `fmt`

Tables and paging — the layer that keeps context from exploding (PLAN.md P2.2).

**class `Table`** — A list of dicts that prints as a fixed-width table and stays small.
- `all()` — Every row, unabridged.
- `df()` — A pandas DataFrame when pandas is available, else a clear error.
- `column(name)`
- `where(predicate)`
- `sort(key, reverse=False)`
- `total(column)`
- `__str__()`

**class `PageStore`** — Holds oversized tool output and hands it out a page at a time.
- `capture(text)`
- `n_pages(handle)`
- `show(handle, page=1)`
- `handles()`

- `show(handle, page=1)` — Return one page of a stored oversized output.
- `paginate(text)` — Store `text` if it is oversized and return what the model should see.

## `brief`

The environment briefing rendered into the first user message (PLAN.md P3.3).

- `render(client=None, char_budget=CHAR_BUDGET)` — Return the briefing text. Never raises: a section that fails says so and moves on.
