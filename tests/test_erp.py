"""P2.3 — the typed Odoo client, against a live dev container.

These tests mutate the dev Odoo, so they run in dependency order within one module and
assume a freshly-created container (`make devbox` recreates it). They never touch a task's
tests/ or solution/: every expectation here comes from Odoo semantics, not from the
benchmark's rules.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "harness"))

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def erp(dev_container_name):
    """An Erp client talking to the dev container's Odoo through the published port."""
    import subprocess

    from lib.erp import Erp

    key = subprocess.run(
        ["docker", "exec", dev_container_name, "cat", "/etc/odoo/api_key"],
        capture_output=True, check=True,
    ).stdout.decode().strip()
    port = subprocess.run(
        ["docker", "port", dev_container_name, "8069/tcp"], capture_output=True, check=True,
    ).stdout.decode().strip().rsplit(":", 1)[-1]
    return Erp(db="bench", url=f"http://127.0.0.1:{port}", user="admin", password=key)


@pytest.fixture(scope="module")
def catalogue(erp):
    """The finished product, a component, and a vendor that sells the component."""
    suppliers = erp.suppliers()
    assert len(suppliers), "the scenario should ship vendor offers"
    offer = suppliers.all()[0]
    return {
        "product_id": offer["product_id"],
        "vendor_id": offer["vendor_id"],
        "min_qty": offer["min_qty"],
        "price": offer["price"],
    }


# -- reads -------------------------------------------------------------------
def test_reads_return_bounded_tables(erp):
    stock = erp.stock()
    assert len(stock) > 0
    text = str(stock)
    assert "product" in text
    # Rendering is capped even when the underlying data is not.
    assert len(text.splitlines()) <= 44


def test_stock_counts_internal_locations_only(erp):
    from lib.erp import INTERNAL_LOCATION_DOMAIN

    everywhere = erp.call("stock.quant", "search_count", [[]])
    internal = erp.call("stock.quant", "search_count", [[INTERNAL_LOCATION_DOMAIN]])
    assert internal <= everywhere
    for row in erp.stock():
        assert row["free"] == pytest.approx(row["on_hand"] - row["reserved"])


def test_suppliers_surface_vendor_internal_notes(erp):
    rows = erp.suppliers().all()
    assert rows and all("vendor_notes" in row for row in rows)
    # Notes arrive as plain text, not the HTML Odoo stores.
    assert not any("<p>" in (row["vendor_notes"] or "") for row in rows)


def test_fields_lists_real_field_names(erp):
    names = set(erp.fields("stock.move").column("name"))
    assert {"quantity", "picked", "product_uom_qty"} <= names
    assert "quantity_done" not in names  # renamed in Odoo 17+; PLAN assumed it might exist


def test_bad_field_raises_a_readable_odoo_error(erp):
    from lib.erp import OdooError

    with pytest.raises(OdooError) as excinfo:
        erp.search_read("sale.order", [], ["definitely_not_a_field"], limit=1)
    message = str(excinfo.value)
    assert "sale.order.search_read" in message
    assert len(message) < 900          # bounded, not a full server traceback
    assert "harness/lib/erp.py" not in message


def test_empty_result_is_an_empty_table_not_an_error(erp):
    assert len(erp.purchase_orders(state="purchase")) == 0 or True
    assert str(erp.sales_orders(ids=[999999])) == "sale.order (0 rows)"


# -- purchasing flow ---------------------------------------------------------
def test_po_create_confirm_receive_moves_stock(erp, catalogue):
    product_id = catalogue["product_id"]
    qty = max(catalogue["min_qty"], 1)

    before = {r["product_id"]: r["on_hand"] for r in erp.stock([product_id])}
    po_id = erp.create_po(catalogue["vendor_id"], [(product_id, qty)], origin="test-po")
    assert isinstance(po_id, int)

    # price_unit was filled from supplierinfo even though we did not pass one.
    line = erp.po_lines([po_id]).all()[0]
    assert line["price_unit"] > 0
    assert line["price_unit"] == pytest.approx(catalogue["price"])

    erp.confirm_po(po_id)
    assert erp.purchase_orders(ids=[po_id]).all()[0]["state"] == "purchase"

    received = erp.receive(po_id, force=True)      # goods are not due yet; this tests the flow
    assert received, "confirming a PO should create a receipt to validate"

    after = {r["product_id"]: r["on_hand"] for r in erp.stock([product_id])}
    assert after.get(product_id, 0) == pytest.approx(before.get(product_id, 0) + qty)
    assert erp.po_lines([po_id]).all()[0]["qty_received"] == pytest.approx(qty)


def test_write_log_records_mutations_but_not_reads(erp):
    erp.write_log.clear()
    erp.stock()
    erp.sales_orders()
    assert erp.write_log == []
    erp.call("res.partner", "search_count", [[]])
    assert erp.write_log == []


# -- manufacturing flow ------------------------------------------------------
def test_mo_create_confirm_produce_consumes_and_produces(erp):
    boms = erp.boms()
    if not len(boms):
        pytest.skip("this scenario has no manufacturing route")
    bom = boms.all()[0]
    finished = erp.search_read(
        "mrp.bom", [("id", "=", bom["bom_id"])], ["product_id", "product_tmpl_id"], limit=1)[0]
    product_id = finished["product_id"][0] if finished["product_id"] else erp.search_read(
        "product.product", [("product_tmpl_id", "=", finished["product_tmpl_id"][0])],
        ["id"], limit=1)[0]["id"]

    component_id = bom["component_id"]
    needed = bom["comp_qty"]
    # Make sure the components exist, buying them if the scenario starts empty.
    have = {r["product_id"]: r["free"] for r in erp.stock([component_id])}
    if have.get(component_id, 0) < needed:
        offers = erp.suppliers([component_id]).all()
        if not offers:
            pytest.skip("component has no vendor to buy from")
        buy_qty = max(offers[0]["min_qty"], needed)
        po_id = erp.create_po(offers[0]["vendor_id"], [(component_id, buy_qty)])
        erp.confirm_po(po_id)
        erp.receive(po_id, force=True)

    before = {r["product_id"]: r["on_hand"] for r in erp.stock([product_id, component_id])}
    mo_id = erp.create_mo(product_id, 1)
    erp.confirm_mo(mo_id)
    erp.produce(mo_id)

    assert erp.productions(ids=[mo_id]).all()[0]["state"] == "done"
    after = {r["product_id"]: r["on_hand"] for r in erp.stock([product_id, component_id])}
    assert after.get(product_id, 0) > before.get(product_id, 0)
    assert after.get(component_id, 0) < before.get(component_id, 0)


# -- sales flow --------------------------------------------------------------
def test_so_confirm_deliver_invoice_post(erp, catalogue):
    product_id = catalogue["product_id"]
    customer = erp.search_read(
        "res.partner", [("customer_rank", ">", 0)], ["name"], limit=1)
    if not customer:
        customer = erp.search_read("res.partner", [], ["name"], limit=1)
    partner_id = customer[0]["id"]

    # Make sure there is something to ship.
    free = {r["product_id"]: r["free"] for r in erp.stock([product_id])}
    if free.get(product_id, 0) < 1:
        po_id = erp.create_po(catalogue["vendor_id"],
                              [(product_id, max(catalogue["min_qty"], 1))])
        erp.confirm_po(po_id)
        erp.receive(po_id, force=True)

    so_id = erp.call("sale.order", "create", [{
        "partner_id": partner_id,
        "order_line": [(0, 0, {"product_id": product_id, "product_uom_qty": 1})],
    }])
    erp.call("sale.order", "action_confirm", [[so_id]])
    assert erp.sales_orders(ids=[so_id]).all()[0]["state"] == "sale"

    delivered = erp.deliver(so_id)
    assert delivered, "confirming a sales order should create a delivery"
    assert erp.sales_orders(ids=[so_id]).all()[0]["delivery_status"] == "full"

    invoice_ids = erp.invoice(so_id)
    assert invoice_ids, "a delivered order should be invoiceable"
    erp.post(invoice_ids)
    posted = erp.get("account.move", invoice_ids, ["state", "amount_total"]).all()
    assert all(row["state"] == "posted" for row in posted)
    assert all(row["amount_total"] > 0 for row in posted)


# -- planning helpers ---------------------------------------------------------
def test_feasible_vendors_excludes_those_who_cannot_make_the_date(erp, catalogue):
    """The arithmetic behind 17 of 23 stock-harness failures, as a function."""
    offers = erp.suppliers([catalogue["product_id"]]).all()
    slowest = max(o["delay_days"] or 0 for o in offers)
    fastest = min(o["delay_days"] or 0 for o in offers)
    from datetime import datetime, timedelta
    today = datetime.utcnow()

    # A due date only the fastest vendor can meet.
    tight = (today + timedelta(days=fastest, hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    table = erp.feasible_vendors(catalogue["product_id"], 5, tight)
    assert all(row["delay_days"] <= fastest for row in table.all()), str(table)
    assert all(row["arrives"] <= tight for row in table.all())

    # A due date everyone can meet lists everyone, cheapest first.
    loose = (today + timedelta(days=slowest + 5)).strftime("%Y-%m-%d %H:%M:%S")
    table = erp.feasible_vendors(catalogue["product_id"], 5, loose)
    assert len(table) == len(offers)
    totals = [row["line_total"] for row in table.all()]
    assert totals == sorted(totals)

    # A due date nobody can meet is an empty table, not an exception. (Some vendors
    # ship same-day, so "today" is feasible; yesterday is not.)
    yesterday = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    assert len(erp.feasible_vendors(catalogue["product_id"], 5, yesterday)) == 0


def test_feasible_vendors_rounds_up_to_the_minimum_quantity(erp, catalogue):
    offer = max(erp.suppliers([catalogue["product_id"]]).all(), key=lambda o: o["min_qty"] or 0)
    if (offer["min_qty"] or 0) < 2:
        pytest.skip("no vendor with a minimum above 1")
    from datetime import datetime, timedelta
    loose = (datetime.utcnow() + timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
    row = next(r for r in erp.feasible_vendors(catalogue["product_id"], 1, loose).all()
               if r["vendor_id"] == offer["vendor_id"])
    assert row["order_qty"] == offer["min_qty"]
    assert row["line_total"] == pytest.approx(offer["price"] * offer["min_qty"])


def test_earliest_build_accounts_for_component_lead_times(erp):
    boms = erp.boms()
    if not len(boms):
        pytest.skip("no manufacturing route in this scenario")
    bom = boms.all()[0]
    finished = erp.search_read("mrp.bom", [("id", "=", bom["bom_id"])],
                               ["product_id", "product_tmpl_id"], limit=1)[0]
    product_id = (finished["product_id"][0] if finished["product_id"] else
                  erp.search_read("product.product",
                                  [("product_tmpl_id", "=", finished["product_tmpl_id"][0])],
                                  ["id"], limit=1)[0]["id"])
    plan = erp.earliest_build(product_id, 10_000)      # far beyond any stock
    assert plan["components"], plan
    assert plan["date_start"] >= erp.today()
    for comp in plan["components"]:
        if comp.get("ready") is not None and comp.get("buy"):
            assert comp["ready"] <= plan["date_start"]     # start waits for the slowest part
            assert comp["from"]


# -- write-time guards --------------------------------------------------------
def test_create_po_refuses_a_date_the_vendor_cannot_meet(erp):
    """The 17-of-23 mistake, stopped at the moment it would be written."""
    from lib.erp import OdooError

    offers = [o for o in erp.suppliers().all() if (o["delay_days"] or 0) >= 3]
    if not offers:
        pytest.skip("no vendor with a 3+ day lead time")
    o = offers[0]
    with pytest.raises(OdooError) as excinfo:
        erp.create_po(o["vendor_id"], [(o["product_id"], max(o["min_qty"], 1))],
                      date_planned=erp.today())
    message = str(excinfo.value)
    assert "earliest delivery" in message and "feasible_vendors" in message

    # The date it names is accepted.
    earliest = message.split("earliest delivery ")[1][:10]
    po_id = erp.create_po(o["vendor_id"], [(o["product_id"], max(o["min_qty"], 1))],
                          date_planned=f"{earliest} 08:00:00")
    assert isinstance(po_id, int)
    erp.cancel("purchase.order", po_id)

    # force=True is the escape hatch.
    po_id = erp.create_po(o["vendor_id"], [(o["product_id"], max(o["min_qty"], 1))],
                          date_planned=erp.today(), force=True)
    erp.cancel("purchase.order", po_id)


def test_receive_refuses_goods_that_are_not_due(erp, catalogue):
    from lib.erp import OdooError

    from datetime import datetime, timedelta
    later = (datetime.utcnow() + timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
    po_id = erp.create_po(catalogue["vendor_id"], [(catalogue["product_id"], max(catalogue["min_qty"], 1))],
                          date_planned=later)
    erp.confirm_po(po_id)
    try:
        with pytest.raises(OdooError) as excinfo:
            erp.receive(po_id)
        assert "not due until" in str(excinfo.value)
        assert erp.po_lines([po_id]).all()[0]["qty_received"] == 0   # nothing was fabricated
    finally:
        erp.cancel("purchase.order", po_id)


def test_create_mo_refuses_a_start_before_components_can_exist(erp):
    from lib.erp import OdooError

    boms = erp.boms()
    if not len(boms):
        pytest.skip("no manufacturing route in this scenario")
    bom = boms.all()[0]
    finished = erp.search_read("mrp.bom", [("id", "=", bom["bom_id"])],
                               ["product_id", "product_tmpl_id"], limit=1)[0]
    product_id = (finished["product_id"][0] if finished["product_id"] else
                  erp.search_read("product.product",
                                  [("product_tmpl_id", "=", finished["product_tmpl_id"][0])],
                                  ["id"], limit=1)[0]["id"])
    plan = erp.earliest_build(product_id, 10_000)
    if plan["date_start"][:10] <= erp.today()[:10]:
        pytest.skip("components for 10,000 units are somehow on hand")
    with pytest.raises(OdooError) as excinfo:
        erp.create_mo(product_id, 10_000, date_start=erp.today())
    assert "components can be on hand" in str(excinfo.value)
    mo_id = erp.create_mo(product_id, 10_000, date_start=erp.today(), force=True)
    erp.cancel("mrp.production", mo_id)
