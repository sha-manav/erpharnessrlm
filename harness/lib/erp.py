"""A typed Odoo client for procurement and manufacturing work (PLAN.md P2.3).

Why this exists, measured rather than assumed: in config A the model reaches Odoo by
writing throwaway `odoolib` scripts, and it guesses. In one dev trial it produced three
tracebacks in a row from field names that do not exist (`res.partner.supplier_rank`
spelled onto the wrong model, `product.supplierinfo` read with the wrong fields), then ran
out of road on timing rules. Every one of those turns was paid for twice: once in output
tokens, once in the traceback that went into the context forever.

So this module fixes the field names, the flows and the error shape in one place:

* **Reads return `Table`** (`lib.fmt`), which caps at 40 rows and renders Odoo's shapes —
  many2one pairs as names, `False` as empty — so a read costs a bounded number of tokens.
* **Writes are named after the business act** (`create_po`, `receive`, `deliver`,
  `invoice`), and each one documents the Odoo calls it makes.
* **Errors arrive as `OdooError`** carrying the tail of Odoo's own message, not a Python
  traceback through the harness.

Everything is stdlib `xmlrpc.client`: no dependency to install, and `/xmlrpc/2/object`
answers on Odoo 19 (NOTES.md ## ERP-Bench / Odoo RPC — measured).

Field names are the Odoo 19 ones, verified against a live container:
`stock.move.quantity` + `picked` (there is no `quantity_done` in 19), `stock.picking.move_ids`
(no `move_ids_without_package`).
"""

from __future__ import annotations

import os
import xmlrpc.client
from typing import Any, Iterable, Sequence

from .fmt import Table

EXPORTS = ["erp", "Erp", "OdooError"]

ODOO_URL = os.environ.get("ODOO_URL", "http://127.0.0.1:8069")
ODOO_DB = os.environ.get("ODOO_DB", "bench")
ODOO_USER = os.environ.get("ODOO_USER", "admin")
API_KEY_FILE = os.environ.get("ODOO_API_KEY_FILE", "/etc/odoo/api_key")

# Only these locations count as stock we own; `stock.quant` also carries supplier,
# customer, inventory-loss and production virtual locations, and summing those instead is
# the classic way to report inventory that does not exist.
INTERNAL_LOCATION_DOMAIN = ("location_id.usage", "=", "internal")


def _password() -> str:
    for key in ("ODOO_PASSWORD", "ODOO_API_KEY"):
        value = os.environ.get(key)
        if value:
            return value
    try:
        return open(API_KEY_FILE).read().strip()
    except OSError:
        return "pass"


class OdooError(RuntimeError):
    """An Odoo-side failure, carrying Odoo's own message rather than our traceback."""

    def __init__(self, message: str, model: str = "", method: str = ""):
        self.model = model
        self.method = method
        # Odoo faults arrive as a full server traceback with the human-readable reason at
        # the end. The tail is the part worth spending context on.
        text = message.strip()
        if len(text) > 600:
            text = "…" + text[-600:]
        super().__init__(f"{model}.{method}: {text}" if model else text)


def _name_of(value: Any) -> str:
    """`[7, 'Axis Assemblies']` -> `'Axis Assemblies'`; False -> ''."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return value[1]
    return "" if value in (False, None) else str(value)


def _id_of(value: Any) -> int | None:
    if isinstance(value, (list, tuple)) and value:
        return value[0]
    return value if isinstance(value, int) else None


def _ids(value: int | Iterable[int] | None) -> list[int] | None:
    if value is None:
        return None
    if isinstance(value, int):
        return [value]
    return list(value)


class Erp:
    """One authenticated connection to one database."""

    def __init__(self, db: str | None = None, url: str | None = None,
                 user: str | None = None, password: str | None = None):
        self.db = db or ODOO_DB
        self.url = url or ODOO_URL
        self.user = user or ODOO_USER
        self.password = password or _password()
        self.write_log: list[tuple] = []
        self._uid: int | None = None
        self._models = None
        self._clients: dict[str, "Erp"] = {}

    # -- plumbing -------------------------------------------------------------
    @property
    def uid(self) -> int:
        if self._uid is None:
            common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", allow_none=True)
            try:
                uid = common.authenticate(self.db, self.user, self.password, {})
            except Exception as exc:  # noqa: BLE001
                raise OdooError(str(exc), "auth", self.db) from None
            if not uid:
                raise OdooError(f"authentication refused for {self.user}@{self.db}", "auth", self.db)
            self._uid = uid
        return self._uid

    @property
    def models(self):
        if self._models is None:
            self._models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)
        return self._models

    def on(self, db: str) -> "Erp":
        """A client for another database on the same server (used by `state`)."""
        if db not in self._clients:
            self._clients[db] = Erp(db, self.url, self.user, self.password)
        return self._clients[db]

    def call(self, model: str, method: str, args: Sequence = (), kwargs: dict | None = None):
        """Raw `execute_kw`. Every non-read call is appended to `write_log`."""
        kwargs = kwargs or {}
        try:
            result = self.models.execute_kw(
                self.db, self.uid, self.password, model, method, list(args), kwargs
            )
        except xmlrpc.client.Fault as fault:
            raise OdooError(fault.faultString, model, method) from None
        except Exception as exc:  # noqa: BLE001 - transport errors read the same way
            raise OdooError(str(exc), model, method) from None
        if method not in ("read", "search", "search_read", "search_count", "fields_get"):
            self.write_log.append((model, method, list(args), kwargs, result))
        return result

    # -- generic reads --------------------------------------------------------
    def search_read(self, model: str, domain: list, fields: list[str],
                    limit: int = 80, order: str | None = None) -> list[dict]:
        kwargs: dict[str, Any] = {"fields": fields, "limit": limit}
        if order:
            kwargs["order"] = order
        return self.call(model, "search_read", [domain], kwargs)

    def get(self, model: str, ids: int | Iterable[int], fields: list[str] | None = None) -> Table:
        rows = self.call(model, "read", [_ids(ids)], {"fields": fields} if fields else {})
        return Table(rows)

    def fields(self, model: str, like: str | None = None) -> Table:
        """What fields a model really has — the antidote to guessing names."""
        raw = self.call(model, "fields_get", [], {"attributes": ["type", "string", "relation"]})
        rows = [
            {"name": name, "type": spec.get("type"), "relation": spec.get("relation") or "",
             "label": spec.get("string")}
            for name, spec in sorted(raw.items())
            if not like or like.lower() in name.lower() or like.lower() in str(spec.get("string", "")).lower()
        ]
        return Table(rows, ["name", "type", "relation", "label"], f"{model} fields", max_rows=80)

    def count(self, model: str, domain: list | None = None) -> int:
        return self.call(model, "search_count", [domain or []])

    # -- domain reads ---------------------------------------------------------
    def sales_orders(self, state: str | None = None, due_before: str | None = None,
                     ids: Iterable[int] | None = None) -> Table:
        domain: list = []
        if ids is not None:
            domain.append(("id", "in", list(ids)))
        if state:
            domain.append(("state", "=", state))
        if due_before:
            domain.append(("commitment_date", "<=", due_before))
        rows = self.search_read(
            "sale.order", domain,
            ["name", "partner_id", "state", "commitment_date", "amount_total",
             "delivery_status", "invoice_status"],
            limit=200, order="commitment_date asc, id asc",
        )
        return Table(
            [{"id": r["id"], "name": r["name"], "partner": _name_of(r["partner_id"]),
              "state": r["state"], "commitment_date": r["commitment_date"],
              "amount_total": r["amount_total"], "delivery_status": r.get("delivery_status"),
              "invoice_status": r.get("invoice_status")} for r in rows],
            ["id", "name", "partner", "state", "commitment_date", "amount_total",
             "delivery_status", "invoice_status"], "sale.order",
        )

    def so_lines(self, so_ids: Iterable[int]) -> Table:
        rows = self.search_read(
            "sale.order.line", [("order_id", "in", list(so_ids))],
            ["order_id", "product_id", "product_uom_qty", "qty_delivered", "qty_invoiced",
             "price_unit"], limit=500, order="order_id asc, id asc",
        )
        return Table(
            [{"so": _name_of(r["order_id"]), "product_id": _id_of(r["product_id"]),
              "product": _name_of(r["product_id"]), "product_uom_qty": r["product_uom_qty"],
              "qty_delivered": r["qty_delivered"], "qty_invoiced": r["qty_invoiced"],
              "price_unit": r["price_unit"]} for r in rows],
            ["so", "product_id", "product", "product_uom_qty", "qty_delivered",
             "qty_invoiced", "price_unit"], "sale.order.line",
        )

    def stock(self, product_ids: Iterable[int] | None = None) -> Table:
        """On-hand / reserved / free per product, summed over internal locations only."""
        domain: list = [INTERNAL_LOCATION_DOMAIN]
        if product_ids is not None:
            domain.append(("product_id", "in", list(product_ids)))
        rows = self.search_read(
            "stock.quant", domain, ["product_id", "quantity", "reserved_quantity"], limit=1000,
        )
        totals: dict[int, dict] = {}
        for row in rows:
            pid = _id_of(row["product_id"])
            entry = totals.setdefault(
                pid, {"product_id": pid, "product": _name_of(row["product_id"]),
                      "on_hand": 0.0, "reserved": 0.0})
            entry["on_hand"] += row["quantity"] or 0.0
            entry["reserved"] += row["reserved_quantity"] or 0.0
        for entry in totals.values():
            entry["free"] = entry["on_hand"] - entry["reserved"]
        return Table(sorted(totals.values(), key=lambda r: r["product"]),
                     ["product_id", "product", "on_hand", "reserved", "free"], "stock on hand")

    def boms(self, product_ids: Iterable[int] | None = None) -> Table:
        domain: list = []
        if product_ids is not None:
            # A BOM points at either the variant or the template, so match both.
            templates = {
                _id_of(r["product_tmpl_id"])
                for r in self.search_read(
                    "product.product", [("id", "in", list(product_ids))], ["product_tmpl_id"], limit=500)
            }
            domain = ["|", ("product_id", "in", list(product_ids)),
                      ("product_tmpl_id", "in", list(templates))]
        bom_rows = self.search_read(
            "mrp.bom", domain, ["product_tmpl_id", "product_id", "product_qty", "bom_line_ids", "type"],
            limit=200)
        line_ids = [lid for row in bom_rows for lid in row.get("bom_line_ids", [])]
        lines = self.call(
            "mrp.bom.line", "read", [line_ids],
            {"fields": ["bom_id", "product_id", "product_qty"]}) if line_ids else []
        by_bom: dict[int, list] = {}
        for line in lines:
            by_bom.setdefault(_id_of(line["bom_id"]), []).append(line)

        out = []
        for bom in bom_rows:
            product = _name_of(bom["product_id"]) or _name_of(bom["product_tmpl_id"])
            for line in by_bom.get(bom["id"], []):
                out.append({
                    "bom_id": bom["id"], "product": product, "bom_qty": bom["product_qty"],
                    "component_id": _id_of(line["product_id"]),
                    "component": _name_of(line["product_id"]),
                    "comp_qty": line["product_qty"],
                })
            if not by_bom.get(bom["id"]):
                out.append({"bom_id": bom["id"], "product": product, "bom_qty": bom["product_qty"],
                            "component_id": None, "component": "(no components)", "comp_qty": 0})
        return Table(out, ["bom_id", "product", "bom_qty", "component_id", "component", "comp_qty"],
                     "mrp.bom")

    def suppliers(self, product_ids: Iterable[int] | None = None) -> Table:
        """Vendor offers from `product.supplierinfo`.

        There is no `max_qty` field in Odoo: a vendor's ceiling, when a task states one,
        lives in `res.partner.comment` (Internal Notes). `vendor_notes` carries it here so
        the agent does not have to know to go looking.
        """
        domain: list = []
        if product_ids is not None:
            templates = {
                _id_of(r["product_tmpl_id"])
                for r in self.search_read(
                    "product.product", [("id", "in", list(product_ids))], ["product_tmpl_id"], limit=500)
            }
            domain = ["|", ("product_id", "in", list(product_ids)),
                      ("product_tmpl_id", "in", list(templates))]
        rows = self.search_read(
            "product.supplierinfo", domain,
            ["product_id", "product_tmpl_id", "partner_id", "min_qty", "price", "delay",
             "currency_id"], limit=500, order="product_tmpl_id asc, price asc")
        variants = self._variants_of_templates(
            {_id_of(r["product_tmpl_id"]) for r in rows if r["product_tmpl_id"]})
        partner_ids = sorted({_id_of(r["partner_id"]) for r in rows if r["partner_id"]})
        notes = {}
        if partner_ids:
            for partner in self.call("res.partner", "read", [partner_ids], {"fields": ["comment"]}):
                notes[partner["id"]] = _strip_html(partner.get("comment") or "")
        return Table(
            [{"product_id": _id_of(r["product_id"]) or variants.get(_id_of(r["product_tmpl_id"])),
              "product": _name_of(r["product_id"]) or _name_of(r["product_tmpl_id"]),
              "vendor_id": _id_of(r["partner_id"]), "vendor": _name_of(r["partner_id"]),
              "min_qty": r["min_qty"], "price": r["price"], "delay_days": r["delay"],
              "currency": _name_of(r["currency_id"]),
              "vendor_notes": notes.get(_id_of(r["partner_id"]), "")} for r in rows],
            ["product_id", "product", "vendor_id", "vendor", "min_qty", "price", "delay_days",
             "currency", "vendor_notes"], "product.supplierinfo")

    def _variants_of_templates(self, template_ids: Iterable[int]) -> dict[int, int]:
        """template id -> one variant id, for offers recorded against the template."""
        template_ids = [t for t in template_ids if t]
        if not template_ids:
            return {}
        rows = self.search_read(
            "product.product", [("product_tmpl_id", "in", template_ids)],
            ["product_tmpl_id"], limit=1000)
        mapping: dict[int, int] = {}
        for row in rows:
            mapping.setdefault(_id_of(row["product_tmpl_id"]), row["id"])
        return mapping

    def purchase_orders(self, state: str | None = None, ids: Iterable[int] | None = None) -> Table:
        domain: list = []
        if ids is not None:
            domain.append(("id", "in", list(ids)))
        if state:
            domain.append(("state", "=", state))
        rows = self.search_read(
            "purchase.order", domain,
            ["name", "partner_id", "state", "date_planned", "amount_total", "receipt_status",
             "origin"], limit=200, order="id asc")
        return Table(
            [{"id": r["id"], "name": r["name"], "vendor": _name_of(r["partner_id"]),
              "state": r["state"], "date_planned": r["date_planned"],
              "amount_total": r["amount_total"], "receipt_status": r.get("receipt_status"),
              "origin": r.get("origin") or ""} for r in rows],
            ["id", "name", "vendor", "state", "date_planned", "amount_total", "receipt_status",
             "origin"], "purchase.order")

    def po_lines(self, po_ids: Iterable[int]) -> Table:
        rows = self.search_read(
            "purchase.order.line", [("order_id", "in", list(po_ids))],
            ["order_id", "product_id", "product_qty", "price_unit", "date_planned",
             "qty_received"], limit=500, order="order_id asc, id asc")
        return Table(
            [{"po": _name_of(r["order_id"]), "product_id": _id_of(r["product_id"]),
              "product": _name_of(r["product_id"]), "product_qty": r["product_qty"],
              "price_unit": r["price_unit"], "date_planned": r["date_planned"],
              "qty_received": r["qty_received"]} for r in rows],
            ["po", "product_id", "product", "product_qty", "price_unit", "date_planned",
             "qty_received"], "purchase.order.line")

    def productions(self, state: str | None = None, ids: Iterable[int] | None = None) -> Table:
        domain: list = []
        if ids is not None:
            domain.append(("id", "in", list(ids)))
        if state:
            domain.append(("state", "=", state))
        rows = self.search_read(
            "mrp.production", domain,
            ["name", "product_id", "product_qty", "state", "date_start", "date_finished",
             "components_availability"], limit=200, order="id asc")
        return Table(
            [{"id": r["id"], "name": r["name"], "product": _name_of(r["product_id"]),
              "product_qty": r["product_qty"], "state": r["state"], "date_start": r["date_start"],
              "date_finished": r.get("date_finished"),
              "components_availability": r.get("components_availability")} for r in rows],
            ["id", "name", "product", "product_qty", "state", "date_start", "date_finished",
             "components_availability"], "mrp.production")

    def pickings(self, picking_type: str | None = None, state: str | None = None,
                 origin: str | None = None) -> Table:
        domain: list = []
        if picking_type:
            domain.append(("picking_type_id.code", "=", picking_type))
        if state:
            domain.append(("state", "=", state))
        if origin:
            domain.append(("origin", "like", origin))
        rows = self.search_read(
            "stock.picking", domain,
            ["name", "picking_type_id", "origin", "state", "scheduled_date"],
            limit=200, order="scheduled_date asc, id asc")
        return Table(
            [{"id": r["id"], "name": r["name"], "picking_type": _name_of(r["picking_type_id"]),
              "origin": r.get("origin") or "", "state": r["state"],
              "scheduled_date": r["scheduled_date"]} for r in rows],
            ["id", "name", "picking_type", "origin", "state", "scheduled_date"], "stock.picking")

    def invoices(self, move_type: str = "out_invoice", state: str | None = None) -> Table:
        domain: list = [("move_type", "=", move_type)] if move_type else []
        if state:
            domain.append(("state", "=", state))
        rows = self.search_read(
            "account.move", domain,
            ["name", "partner_id", "state", "amount_total", "invoice_origin", "payment_state"],
            limit=200, order="id asc")
        return Table(
            [{"id": r["id"], "name": r["name"], "partner": _name_of(r["partner_id"]),
              "state": r["state"], "amount_total": r["amount_total"],
              "invoice_origin": r.get("invoice_origin") or ""} for r in rows],
            ["id", "name", "partner", "state", "amount_total", "invoice_origin"], "account.move")

    # -- writes ---------------------------------------------------------------
    def create_po(self, vendor_id: int, lines: Sequence[tuple], date_planned: str | None = None,
                  origin: str | None = None) -> int:
        """Create a purchase order. `lines` = [(product_id, qty[, price_unit[, date_planned]])].

        Calls `purchase.order.create` with `order_line` command tuples. Odoo's onchange
        defaults do not fire over RPC, so an omitted `price_unit` is looked up from the
        vendor's `product.supplierinfo` tier for that quantity and written explicitly —
        otherwise the line silently prices at 0.00.
        """
        commands = []
        for line in lines:
            product_id, qty = line[0], line[1]
            price = line[2] if len(line) > 2 and line[2] is not None else self.vendor_price(
                vendor_id, product_id, qty)
            line_date = line[3] if len(line) > 3 and line[3] else date_planned
            values = {"product_id": product_id, "product_qty": qty, "price_unit": price}
            if line_date:
                values["date_planned"] = line_date
            commands.append((0, 0, values))

        order: dict[str, Any] = {"partner_id": vendor_id, "order_line": commands}
        if date_planned:
            order["date_planned"] = date_planned
        if origin:
            order["origin"] = origin
        return self.call("purchase.order", "create", [order])

    def vendor_price(self, vendor_id: int, product_id: int, qty: float) -> float:
        """The supplierinfo price for this vendor/product at this quantity tier."""
        template = self.search_read(
            "product.product", [("id", "=", product_id)], ["product_tmpl_id"], limit=1)
        template_id = _id_of(template[0]["product_tmpl_id"]) if template else None
        offers = self.search_read(
            "product.supplierinfo",
            ["&", ("partner_id", "=", vendor_id),
             "|", ("product_id", "=", product_id), ("product_tmpl_id", "=", template_id)],
            ["min_qty", "price"], limit=50, order="min_qty desc")
        for offer in offers:
            if qty >= (offer["min_qty"] or 0):
                return offer["price"]
        return offers[-1]["price"] if offers else 0.0

    def confirm_po(self, po_id: int):
        """`purchase.order.button_confirm`."""
        return self.call("purchase.order", "button_confirm", [[po_id]])

    def receive(self, po_id: int) -> list[int]:
        """Validate every incoming picking of a PO. Returns the picking ids validated."""
        pickings = self.search_read(
            "stock.picking", [("id", "in", self._po_picking_ids(po_id)),
                              ("state", "not in", ("done", "cancel"))], ["name"], limit=50)
        for picking in pickings:
            self._validate_picking(picking["id"])
        return [p["id"] for p in pickings]

    def _po_picking_ids(self, po_id: int) -> list[int]:
        rows = self.call("purchase.order", "read", [[po_id]], {"fields": ["picking_ids"]})
        return rows[0]["picking_ids"] if rows else []

    def create_mo(self, product_id: int, qty: float, bom_id: int | None = None,
                  date_start: str | None = None) -> int:
        """`mrp.production.create`. Odoo picks the BOM if one is not named."""
        values: dict[str, Any] = {"product_id": product_id, "product_qty": qty}
        if bom_id:
            values["bom_id"] = bom_id
        if date_start:
            values["date_start"] = date_start
        return self.call("mrp.production", "create", [values])

    def confirm_mo(self, mo_id: int):
        """`mrp.production.action_confirm`."""
        return self.call("mrp.production", "action_confirm", [[mo_id]])

    def produce(self, mo_id: int, qty: float | None = None):
        """Reserve, set `qty_producing`, consume the components, and mark done.

        The component consumption has to be written explicitly. Odoo fills a raw move's
        `quantity` from an onchange that does not fire over RPC, so `button_mark_done`
        with untouched raw moves finishes the order having consumed **nothing**: the MO
        reads `state = done` while its raw moves end `state = cancel, quantity = 0`.
        Verified on the live image — this is the silent wrong answer this client exists to
        prevent, and no amount of reading `mrp.production` afterwards reveals it.

        `button_mark_done` may also return a wizard action (consumption warning, immediate
        production, backorder); `_run_wizard` completes whichever one appears.
        """
        self.call("mrp.production", "action_assign", [[mo_id]])
        rows = self.call("mrp.production", "read", [[mo_id]],
                         {"fields": ["product_qty", "move_raw_ids"]})
        ordered = rows[0]["product_qty"]
        producing = ordered if qty is None else qty
        self.call("mrp.production", "write", [[mo_id], {"qty_producing": producing}])

        raw_ids = rows[0].get("move_raw_ids") or []
        if raw_ids:
            moves = self.call("stock.move", "read", [raw_ids],
                              {"fields": ["product_uom_qty", "state"]})
            # Scale the bill of materials when producing less than the whole order.
            share = (producing / ordered) if ordered else 1.0
            for move in moves:
                if move["state"] in ("done", "cancel"):
                    continue
                self.call("stock.move", "write", [[move["id"]], {
                    "quantity": (move["product_uom_qty"] or 0.0) * share,
                    "picked": True,
                }])

        result = self.call("mrp.production", "button_mark_done", [[mo_id]])
        return self._run_wizard(result, active_model="mrp.production", active_ids=[mo_id])

    def deliver(self, so_id: int) -> list[int]:
        """Validate every outgoing picking of a sales order."""
        rows = self.call("sale.order", "read", [[so_id]], {"fields": ["picking_ids"]})
        picking_ids = rows[0]["picking_ids"] if rows else []
        pickings = self.search_read(
            "stock.picking", [("id", "in", picking_ids), ("state", "not in", ("done", "cancel"))],
            ["name"], limit=50)
        for picking in pickings:
            self._validate_picking(picking["id"])
        return [p["id"] for p in pickings]

    def invoice(self, so_id: int) -> list[int]:
        """Create the customer invoice(s) for a delivered sales order.

        Uses the `sale.advance.payment.inv` wizard with `advance_payment_method='delivered'`
        and returns the new `account.move` ids (the difference in `invoice_ids` around the
        call — the wizard's own return value is a UI action, not the ids).
        """
        before = set(self._so_invoice_ids(so_id))
        context = {"active_model": "sale.order", "active_ids": [so_id], "active_id": so_id}
        wizard_id = self.call(
            "sale.advance.payment.inv", "create", [{"advance_payment_method": "delivered"}],
            {"context": context})
        self._call_tolerating_unserializable_reply(
            "sale.advance.payment.inv", "create_invoices", [[wizard_id]], {"context": context})
        created = sorted(set(self._so_invoice_ids(so_id)) - before)
        if not created:
            raise OdooError(
                "create_invoices produced no invoice; check that the order is confirmed "
                "and something has been delivered",
                "sale.advance.payment.inv", "create_invoices")
        return created

    def _call_tolerating_unserializable_reply(self, model: str, method: str,
                                              args: Sequence, kwargs: dict | None = None):
        """Run a call whose *reply* Odoo cannot marshal, and swallow only that failure.

        `sale.advance.payment.inv.create_invoices` returns a UI action containing nulls,
        and Odoo's XML-RPC layer raises "cannot marshal None unless allow_none is enabled"
        while writing the response — after the transaction has committed. Verified on the
        live image: the invoice exists afterwards. Every caller of this helper checks the
        effect itself, so a genuine failure is still caught.
        """
        try:
            return self.call(model, method, args, kwargs)
        except OdooError as exc:
            if "cannot marshal None" not in str(exc):
                raise
            return None

    def _so_invoice_ids(self, so_id: int) -> list[int]:
        rows = self.call("sale.order", "read", [[so_id]], {"fields": ["invoice_ids"]})
        return rows[0]["invoice_ids"] if rows else []

    def post(self, invoice_ids: int | Iterable[int]):
        """`account.move.action_post`."""
        return self.call("account.move", "action_post", [_ids(invoice_ids)])

    def cancel(self, model: str, ids: int | Iterable[int]):
        """Cancel records with whichever cancel method the model exposes."""
        methods = {
            "purchase.order": "button_cancel",
            "sale.order": "action_cancel",
            "mrp.production": "action_cancel",
            "stock.picking": "action_cancel",
            "account.move": "button_cancel",
        }
        return self.call(model, methods.get(model, "action_cancel"), [_ids(ids)])

    def _validate_picking(self, picking_id: int):
        """Reserve, write done quantities, validate, and clear any backorder wizard.

        Odoo 19 names the done quantity `stock.move.quantity` and needs `picked = True`;
        there is no `quantity_done` field (verified against the live image). A picking
        validated without those is silently a zero-quantity transfer.
        """
        self.call("stock.picking", "action_assign", [[picking_id]])
        moves = self.search_read(
            "stock.move", [("picking_id", "=", picking_id), ("state", "not in", ("done", "cancel"))],
            ["product_uom_qty", "quantity"], limit=200)
        for move in moves:
            self.call("stock.move", "write",
                      [[move["id"]], {"quantity": move["product_uom_qty"], "picked": True}])
        result = self.call("stock.picking", "button_validate", [[picking_id]])
        return self._run_wizard(result, active_model="stock.picking", active_ids=[picking_id])

    # A returned action means Odoo wants a wizard answered. Map each wizard to the method
    # that means "yes, proceed as I asked" — for backorders that is "no backorder".
    WIZARD_METHODS = {
        "stock.backorder.confirmation": "process_cancel_backorder",
        "stock.immediate.transfer": "process",
        "mrp.consumption.warning": "action_confirm",
        "mrp.immediate.production": "process",
    }

    def _run_wizard(self, action: Any, active_model: str, active_ids: list[int]):
        """Complete a wizard action returned by a button, recursing while more appear."""
        for _ in range(4):  # a chain of two is normal; four is a runaway
            if not isinstance(action, dict) or not action.get("res_model"):
                return action
            model = action["res_model"]
            method = self.WIZARD_METHODS.get(model)
            if method is None:
                raise OdooError(
                    f"unhandled wizard {model}; inspect it with "
                    f"erp.fields({model!r}) and call it via erp.call(...)",
                    active_model, "wizard")
            context = dict(action.get("context") or {})
            context.setdefault("active_model", active_model)
            context.setdefault("active_ids", active_ids)
            context.setdefault("active_id", active_ids[0])
            wizard_id = action.get("res_id") or self.call(
                model, "create", [{}], {"context": context})
            action = self.call(model, method, [[wizard_id]], {"context": context})
        raise OdooError("wizard chain did not settle after 4 steps", active_model, "wizard")


def _strip_html(text: str) -> str:
    """Odoo stores Internal Notes as HTML; the agent wants the sentence."""
    import re

    plain = re.sub(r"<[^>]+>", " ", text or "")
    plain = plain.replace("&nbsp;", " ").replace("&amp;", "&")
    return " ".join(plain.split())


erp = Erp()
