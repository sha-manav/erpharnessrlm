# Odoo 19 procurement and manufacturing playbook

Everything here is Odoo behaviour, verified against this image. It is generic ERP practice,
not guidance about any particular task.

## Flows

**Purchasing.** Create the order with lines carrying `price_unit` *and* `date_planned`
(Odoo's onchange defaults do not fire over RPC — an omitted price silently becomes 0.00),
then `button_confirm`. **Do not validate the receipt.** Goods arrive at
`order date + supplierinfo.delay`, not when you click; a receipt validated today for a
vendor with a 19-day lead time fabricates stock that does not exist, and the plan fails on
timing however correct everything else is. `date_planned` must be on or after that
arrival date *and* on or before the customer due date it serves — if no vendor can meet
both, say so in the summary rather than inventing a date.

```python
po = erp.create_po(vendor_id, [(product_id, qty)],
                   date_planned="2026-09-21 08:00:00",   # >= order date + delay
                   origin="S00030")                       # price looked up from supplierinfo
erp.confirm_po(po)                                       # leave it confirmed; no receive()
```

`erp.receive(po)` exists for goods that are genuinely due — a receipt whose planned date
has passed — not for closing the loop early. **`force=True` never makes a check pass**: a
forced early receipt is refused at finish as fabricated stock. If a delivery cannot be
covered from stock plus purchases that arrive in time, that is the answer — report it,
do not manufacture it.

**Manufacturing.** Create the MO, confirm, then produce. `erp.produce` reserves, sets
`qty_producing`, **writes the component consumption explicitly** and marks done — without
that last part Odoo finishes the order having consumed nothing.

```python
mo = erp.create_mo(product_id, qty, date_start="2026-09-05 08:00:00")
erp.confirm_mo(mo); erp.produce(mo)
```

**Sales.** Confirm, and set `commitment_date`. Deliver **only what is on hand now** —
a delivery draws from stock, and stock that is still on a purchase order is not on hand.
Invoice what you delivered and post it; an order delivered but not invoiced, or invoiced
but left in draft, is unfinished work. Orders waiting on incoming goods stay confirmed
with their delivery pending.

```python
erp.call("sale.order", "action_confirm", [[so_id]])
erp.deliver(so_id)
erp.post(erp.invoice(so_id))
```

## Planning before acting

Enumerate every feasible option for each shortage before choosing one:

* buy from each vendor that lists the product, at each `min_qty` tier;
* make it, if a BOM exists — then recurse onto its components.

Do not do the date arithmetic by hand — it is where plans go wrong. Two helpers do it
against the live data:

```python
erp.feasible_vendors(product_id, qty, need_by)   # only vendors who can land it in time:
                                                  # arrival date (use as date_planned), MOQ,
                                                  # line total, vendor notes; cheapest first
erp.earliest_build(product_id, qty)              # when an MO can start: per-component
                                                  # stock, what to buy from whom, arrival
```

An empty `feasible_vendors` table means no listed vendor can make the date — cover the
demand from stock or manufacturing, or say so in the summary. Never invent a
`date_planned` earlier than the arrival these return.

Dates are where these plans go wrong. A plan that is correct in vendor, quantity and price
but whose receipt lands one day after the commitment date covers nothing. Check, for every
demand: does what I ordered arrive before it is needed, and are an MO's components on hand
or on a receipt landing before the MO starts?

Cheapest-per-unit is not cheapest overall once minimum quantities and tier prices are in,
but a cheap infeasible plan scores nothing at all.

## Data model

| model | fields that matter |
|---|---|
| `product.product` / `product.template` | `default_code`, `list_price`, `standard_price`, `qty_available` |
| `product.supplierinfo` | `partner_id`, `min_qty`, `price`, `delay` — **no `max_qty` exists**; a vendor's ceiling, if a task states one, is in `res.partner.comment` (Internal Notes) |
| `mrp.bom` / `mrp.bom.line` | `product_qty` (the batch size the components are quoted for), `bom_line_ids` |
| `sale.order` / `.line` | `commitment_date` is the customer due date; `delivery_status`, `invoice_status` |
| `purchase.order` / `.line` | `date_planned` on both header and line; `origin` carries the source reference; `receipt_status` |
| `mrp.production` | `product_qty`, `bom_id`, `date_start`, `qty_producing`, `components_availability` |
| `stock.picking` / `stock.move` | receipts vs deliveries by `picking_type_id.code`; multi-step routes make several pickings per order |
| `stock.quant` | `quantity`, `reserved_quantity` — **internal locations only**, or you will count stock that is not yours |
| `account.move` | `move_type`, `state`, `invoice_origin` |

## Gotchas

* **Underscore methods are not RPC-callable.** Use the wizard, or the `erp` helper.
* **Odoo 19 has no `quantity_done`.** A picking needs `quantity` + `picked = True` on each
  move before `button_validate`, or it validates as a zero-quantity transfer that still
  reads `state = done`.
* **Many2one writes take an id; one2many writes take command tuples** `(0, 0, vals)`.
* **Datetimes are UTC strings**, `"%Y-%m-%d %H:%M:%S"`.
* **Names come from `ir.sequence`** — never invent `S00031`; read it back after create.
* **Confirming a PO adds that vendor to the product's supplier list.** So "is this vendor
  listed?" is not a test you can apply after the fact — decide before ordering.
* **Components must exist before an MO starts**: on hand, or on a receipt landing earlier.
* **Receipt date = order date + `supplierinfo.delay`.** A receipt that lands after the
  customer due date it feeds does not cover that order, however correct the quantities are.
* **Never hardcode ids obtained during a rehearsal.** A snapshot database numbers records
  its own way; look records up by name or domain so the same function runs on both.

## Working economically

Every tool result is re-sent with every later turn. Reads return `Table`, which caps at 40
rows — call `.all()` only when you need the rest, and `show("h3", 2)` to page long output.
Do the arithmetic in the kernel and print the answer, not the raw rows.
