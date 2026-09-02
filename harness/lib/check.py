"""End-state invariants, from ERP practice — never from benchmark grader code.

PLAN.md hard rule 2: every check here is derived from how Odoo procurement actually works
(Appendix B), and none of it was written by reading a task's `tests/` or `solution/`. That
matters for the result to mean anything: if the validators were copied from the grader, a
gain would only show that we told the model the answer.

What these are *for*: the measured failure of config A is not that the model cannot act,
it is that it stops without noticing what it left undone — dangling drafts, an uninvoiced
delivery, a receipt scheduled after the date it is meant to cover. A check that runs before
`finish` turns those into another turn instead of a lost trial.

Two kinds of check:

* **invariants** — generic, always applicable, defined here.
* **rules** — task-specific, defined by the *agent* from the instruction
  (`check.register(Rule(...))`). The harness never encodes a task's rules itself.

`hard` checks block `finish`; soft ones are reported.

v0 implements Appendix B items 1, 3, 4 and 7 (drafts, stock non-negative, supplier
validity, invoicing). Items 2, 5, 6, 8, 9, 10 arrive in P3.5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .fmt import Table

# The module itself is bound in the namespace as `check`; EXPORTS names the extra
# symbols to bind alongside it. Listing "check" here asked for lib.check.check.
EXPORTS = ["Check", "Rule"]

# Documents the agent is responsible for finishing once it has created them.
DRAFT_STATES = {
    "sale.order": ("draft", "sent"),
    "purchase.order": ("draft", "sent", "to approve"),
    "mrp.production": ("draft",),
    "account.move": ("draft",),
}


@dataclass
class Check:
    id: str
    title: str
    hard: bool
    fn: Callable[[object], tuple[bool, str]]


@dataclass
class Rule:
    """A task-specific rule the agent writes from the instruction.

    `fn(client) -> (passed, evidence)`. Evidence is shown whether it passes or fails, so
    write it as the number you checked, not as "ok".
    """

    id: str
    description: str
    fn: Callable[[object], tuple[bool, str]]
    hard: bool = True


@dataclass
class _Registry:
    rules: list[Rule] = field(default_factory=list)


_registry = _Registry()


# -- invariants ---------------------------------------------------------------
def _no_dangling_drafts(client) -> tuple[bool, str]:
    """Appendix B.1 (hard) — nothing the agent created is left unconfirmed.

    A draft PO looks like work done and buys nothing; it is the single most common way to
    end a procurement task having achieved nothing.
    """
    stragglers = []
    for model, states in DRAFT_STATES.items():
        rows = client.search_read(model, [("state", "in", list(states))], ["name", "state"], limit=100)
        stragglers += [f"{model} {r.get('name') or r['id']} ({r['state']})" for r in rows]
    if not stragglers:
        return True, "no draft sale/purchase/manufacturing/invoice records remain"
    shown = ", ".join(stragglers[:8])
    more = f" (+{len(stragglers) - 8} more)" if len(stragglers) > 8 else ""
    return False, f"{len(stragglers)} draft record(s): {shown}{more}"


def _stock_non_negative(client) -> tuple[bool, str]:
    """Appendix B.3 (hard) — no internal location holds a negative quantity.

    Negative on-hand means something was shipped that was never received or produced; the
    end state is arithmetically impossible however good it looks in the order list.
    """
    rows = client.search_read(
        "stock.quant",
        [("location_id.usage", "=", "internal"), ("quantity", "<", 0)],
        ["product_id", "location_id", "quantity"], limit=50)
    if not rows:
        return True, "every internal location is non-negative"
    worst = ", ".join(
        f"{r['product_id'][1] if r['product_id'] else '?'} "
        f"@{r['location_id'][1] if r['location_id'] else '?'} = {r['quantity']}"
        for r in rows[:5])
    return False, f"{len(rows)} negative quant(s): {worst}"


def _supplier_validity(client) -> tuple[bool, str]:
    """Appendix B.4 (hard) — every PO line names a vendor who actually sells that product,
    at or above the vendor's minimum quantity, at that vendor's price for the tier.

    Ordering from a vendor with no `product.supplierinfo` for the product, or below their
    `min_qty`, is not a purchase any ERP would honour.
    """
    orders = client.search_read(
        "purchase.order", [("state", "not in", ("cancel",))],
        ["name", "partner_id", "create_date"], limit=200)
    if not orders:
        return True, "no purchase orders to validate"
    by_id = {o["id"]: o for o in orders}
    lines = client.search_read(
        "purchase.order.line", [("order_id", "in", list(by_id))],
        ["order_id", "product_id", "product_qty", "price_unit"], limit=500)

    problems = []
    for line in lines:
        order = by_id.get(line["order_id"][0] if line["order_id"] else None)
        if not order or not order["partner_id"] or not line["product_id"]:
            continue
        vendor_id, vendor_name = order["partner_id"]
        product_id, product_name = line["product_id"]
        template = client.search_read(
            "product.product", [("id", "=", product_id)], ["product_tmpl_id"], limit=1)
        template_id = template[0]["product_tmpl_id"][0] if template else None
        offers = client.search_read(
            "product.supplierinfo",
            ["&", ("partner_id", "=", vendor_id),
             "|", ("product_id", "=", product_id), ("product_tmpl_id", "=", template_id)],
            ["min_qty", "price", "create_date"], limit=50, order="min_qty desc")
        # Confirming a purchase order makes Odoo *add* the vendor to the product's supplier
        # list (`_add_supplier_to_product`). So after the fact, every vendor "lists" every
        # product it was ordered from, and a naive listing check can never fail. An offer
        # created at or after the order it would justify is the order's own footprint, not
        # evidence that the vendor was a valid source — discount it.
        offers = [
            offer for offer in offers
            if not (offer.get("create_date") and order.get("create_date")
                    and offer["create_date"] >= order["create_date"])
        ]
        if not offers:
            problems.append(f"{order['name']}: {vendor_name} does not list {product_name}")
            continue
        tiers = sorted(offers, key=lambda o: o["min_qty"] or 0)
        smallest = tiers[0]["min_qty"] or 0
        if line["product_qty"] < smallest:
            problems.append(
                f"{order['name']}: {line['product_qty']:g} of {product_name} is below "
                f"{vendor_name}'s minimum {smallest:g}")
            continue
        applicable = [o for o in tiers if line["product_qty"] >= (o["min_qty"] or 0)]
        expected = applicable[-1]["price"] if applicable else tiers[0]["price"]
        if abs((line["price_unit"] or 0) - expected) > 0.011:
            problems.append(
                f"{order['name']}: {product_name} priced {line['price_unit']:.2f}, "
                f"{vendor_name}'s tier price is {expected:.2f}")

    if not problems:
        return True, f"{len(lines)} purchase line(s) match a vendor offer and its tier"
    shown = "; ".join(problems[:5])
    more = f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""
    return False, f"{len(problems)} problem(s): {shown}{more}"


def _invoicing_complete(client) -> tuple[bool, str]:
    """Appendix B.7 (hard) — delivered orders are invoiced and the invoices are posted.

    `invoice_status == 'to invoice'` on a delivered order, or an invoice left in draft, is
    revenue the ERP has not recognised.
    """
    uninvoiced = client.search_read(
        "sale.order",
        [("state", "=", "sale"), ("invoice_status", "=", "to invoice"),
         ("delivery_status", "in", ("full", "partial"))],
        ["name", "delivery_status"], limit=50)
    drafts = client.search_read(
        "account.move", [("move_type", "=", "out_invoice"), ("state", "=", "draft")],
        ["name", "invoice_origin"], limit=50)

    problems = [f"{o['name']} delivered ({o['delivery_status']}) but not invoiced" for o in uninvoiced]
    problems += [f"invoice for {m.get('invoice_origin') or m['id']} still draft" for m in drafts]
    if not problems:
        return True, "every delivered order is invoiced and every invoice is posted"
    shown = "; ".join(problems[:6])
    more = f" (+{len(problems) - 6} more)" if len(problems) > 6 else ""
    return False, f"{len(problems)} problem(s): {shown}{more}"


INVARIANTS: list[Check] = [
    Check("drafts", "no dangling draft documents", True, _no_dangling_drafts),
    Check("stock_non_negative", "stock is non-negative everywhere", True, _stock_non_negative),
    Check("supplier_validity", "PO lines match a vendor offer, MOQ and tier price", True,
          _supplier_validity),
    Check("invoicing", "delivered orders invoiced, invoices posted", True, _invoicing_complete),
]


# -- runner -------------------------------------------------------------------
def _client(client=None):
    if client is not None:
        return client
    from .erp import erp  # imported lazily so `check` works without a live connection

    return erp


def _run(items, client) -> list[dict]:
    rows = []
    for item in items:
        try:
            passed, evidence = item.fn(client)
        except Exception as exc:  # noqa: BLE001 - a broken check must not hide the others
            passed, evidence = False, f"check raised {type(exc).__name__}: {exc}"
        rows.append({
            "check": item.id,
            "hard": "hard" if item.hard else "soft",
            "status": "pass" if passed else "FAIL",
            "evidence": evidence,
        })
    return rows


def invariants(client=None) -> Table:
    """Run the generic end-state invariants."""
    return Table(_run(INVARIANTS, _client(client)),
                 ["check", "hard", "status", "evidence"], "invariants", max_rows=60)


def rules(client=None, rule_list: list[Rule] | None = None) -> Table:
    """Run the agent's task-specific rules."""
    chosen = _registry.rules if rule_list is None else rule_list
    items = [Check(r.id, r.description, r.hard, r.fn) for r in chosen]
    return Table(_run(items, _client(client)),
                 ["check", "hard", "status", "evidence"], "rules", max_rows=60)


def register(rule: Rule) -> Rule:
    """Keep a rule for every later `check.all()`; re-registering an id replaces it."""
    _registry.rules = [r for r in _registry.rules if r.id != rule.id] + [rule]
    return rule


def registered() -> list[Rule]:
    return list(_registry.rules)


def clear() -> None:
    _registry.rules = []


def all(client=None) -> Table:  # noqa: A001 - the contract calls it check.all()
    """Invariants plus every registered rule, in one table."""
    resolved = _client(client)
    rows = _run(INVARIANTS, resolved)
    rows += _run([Check(r.id, r.description, r.hard, r.fn) for r in _registry.rules], resolved)
    return Table(rows, ["check", "hard", "status", "evidence"], "checks", max_rows=60)


def failures(client=None, hard_only: bool = True) -> list[dict]:
    """The rows that failed — what `finish` gates on."""
    return [
        row for row in all(client).all()
        if row["status"] == "FAIL" and (not hard_only or row["hard"] == "hard")
    ]
