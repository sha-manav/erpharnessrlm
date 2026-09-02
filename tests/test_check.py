"""P2.4 — invariants and the finish gate, against a live dev container.

Each seeded violation is created through ordinary Odoo operations, checked, then cleaned
up, so the tests can run in any order against one container. Nothing here reads a task's
tests/ or solution/.
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
    import subprocess

    from lib.erp import Erp

    key = subprocess.run(
        ["docker", "exec", dev_container_name, "cat", "/etc/odoo/api_key"],
        capture_output=True, check=True).stdout.decode().strip()
    port = subprocess.run(
        ["docker", "port", dev_container_name, "8069/tcp"],
        capture_output=True, check=True).stdout.decode().strip().rsplit(":", 1)[-1]
    return Erp(db="bench", url=f"http://127.0.0.1:{port}", user="admin", password=key)


@pytest.fixture(autouse=True)
def clean_registry():
    from lib import check, finish

    check.clear()
    finish.reset()
    yield
    check.clear()
    finish.reset()


@pytest.fixture
def offer(erp):
    rows = erp.suppliers().all()
    assert rows, "the scenario should ship vendor offers"
    return rows[0]


def status_of(table, check_id: str) -> str:
    for row in table.all():
        if row["check"] == check_id:
            return row["status"]
    raise AssertionError(f"no check {check_id!r} in {[r['check'] for r in table.all()]}")


def evidence_of(table, check_id: str) -> str:
    return next(r["evidence"] for r in table.all() if r["check"] == check_id)


# -- clean state --------------------------------------------------------------
def test_untouched_environment_passes_every_invariant(erp):
    """The clean-state case: every invariant must pass before anything is touched.

    This one genuinely needs a pristine scenario, and `tests/test_erp.py` mutates the same
    container, so a full-suite run in the wrong order leaves real orders behind. Skip
    rather than fail: an order left by a sibling test is not a defect in the invariants.
    """
    from lib import check

    touched = erp.count("purchase.order") + erp.count("sale.order") + erp.count("mrp.production")
    if touched:
        pytest.skip(f"container already has {touched} order(s); run `make devbox` for a clean one")

    table = check.invariants(erp)
    failed = [r for r in table.all() if r["status"] == "FAIL"]
    assert not failed, f"clean environment should pass; failed: {failed}"


# -- seeded violations --------------------------------------------------------
def test_draft_purchase_order_is_detected(erp, offer):
    from lib import check

    po_id = erp.create_po(offer["vendor_id"],
                          [(offer["product_id"], max(offer["min_qty"], 1))])
    try:
        table = check.invariants(erp)
        assert status_of(table, "drafts") == "FAIL"
        assert "purchase.order" in evidence_of(table, "drafts")
    finally:
        erp.cancel("purchase.order", po_id)
        erp.call("purchase.order", "unlink", [[po_id]])
    assert status_of(check.invariants(erp), "drafts") == "pass"


def test_vendor_who_does_not_sell_the_product_is_detected(erp, offer):
    from lib import check

    # A partner with *no* supplierinfo for this product. "Any vendor that is not this one"
    # is not enough: these scenarios list four vendors per product, so the obvious pick is
    # usually another legitimate one, and the check rightly passes. Ask Odoo directly, and
    # assert the precondition so the test cannot silently stop testing anything.
    product_id = offer["product_id"]
    template_id = erp.search_read(
        "product.product", [("id", "=", product_id)], ["product_tmpl_id"], limit=1
    )[0]["product_tmpl_id"][0]
    listed = {
        row["partner_id"][0]
        for row in erp.search_read(
            "product.supplierinfo",
            ["|", ("product_id", "=", product_id), ("product_tmpl_id", "=", template_id)],
            ["partner_id"], limit=200)
        if row["partner_id"]
    }
    candidates = erp.search_read("res.partner", [], ["name"], limit=300)
    stranger_id = next(p["id"] for p in candidates if p["id"] not in listed)
    assert not erp.search_read(
        "product.supplierinfo",
        ["&", ("partner_id", "=", stranger_id),
         "|", ("product_id", "=", product_id), ("product_tmpl_id", "=", template_id)],
        ["id"], limit=1), "picked a partner that does sell this product; the test would prove nothing"

    po_id = erp.call("purchase.order", "create", [{
        "partner_id": stranger_id,
        "order_line": [(0, 0, {"product_id": offer["product_id"],
                               "product_qty": max(offer["min_qty"], 1),
                               "price_unit": offer["price"]})],
    }])
    erp.confirm_po(po_id)
    try:
        table = check.invariants(erp)
        assert status_of(table, "supplier_validity") == "FAIL"
        assert "does not list" in evidence_of(table, "supplier_validity")
    finally:
        erp.cancel("purchase.order", po_id)


def test_quantity_below_the_vendor_minimum_is_detected(erp, offer):
    from lib import check

    if (offer["min_qty"] or 0) < 2:
        pytest.skip("this vendor has no minimum to fall below")
    po_id = erp.call("purchase.order", "create", [{
        "partner_id": offer["vendor_id"],
        "order_line": [(0, 0, {"product_id": offer["product_id"], "product_qty": 1,
                               "price_unit": offer["price"]})],
    }])
    erp.confirm_po(po_id)
    try:
        assert status_of(check.invariants(erp), "supplier_validity") == "FAIL"
        assert "below" in evidence_of(check.invariants(erp), "supplier_validity")
    finally:
        erp.cancel("purchase.order", po_id)


def test_wrong_tier_price_is_detected(erp, offer):
    from lib import check

    # Confirming a PO makes Odoo record an offer at that price, so a fixed wrong price
    # becomes a legitimate tier on the *next* run of this test and the seeded violation
    # quietly stops being one. Pick a price above every offer that already exists, which
    # stays wrong however many times this has run before.
    existing = erp.search_read(
        "product.supplierinfo", [("partner_id", "=", offer["vendor_id"])], ["price"], limit=200)
    wrong_price = max([row["price"] or 0 for row in existing] + [offer["price"] or 10]) * 3 + 137.0

    po_id = erp.call("purchase.order", "create", [{
        "partner_id": offer["vendor_id"],
        "order_line": [(0, 0, {"product_id": offer["product_id"],
                               "product_qty": max(offer["min_qty"], 1),
                               "price_unit": wrong_price})],
    }])
    erp.confirm_po(po_id)
    try:
        assert status_of(check.invariants(erp), "supplier_validity") == "FAIL"
        assert "tier price" in evidence_of(check.invariants(erp), "supplier_validity")
    finally:
        erp.cancel("purchase.order", po_id)


def test_delivery_without_an_invoice_is_detected(erp, offer):
    from lib import check

    product_id = offer["product_id"]
    free = {r["product_id"]: r["free"] for r in erp.stock([product_id])}
    if free.get(product_id, 0) < 1:
        po_id = erp.create_po(offer["vendor_id"], [(product_id, max(offer["min_qty"], 1))])
        erp.confirm_po(po_id)
        erp.receive(po_id)

    partner = erp.search_read("res.partner", [("customer_rank", ">", 0)], ["name"], limit=1)
    partner_id = (partner or erp.search_read("res.partner", [], ["name"], limit=1))[0]["id"]
    so_id = erp.call("sale.order", "create", [{
        "partner_id": partner_id,
        "order_line": [(0, 0, {"product_id": product_id, "product_uom_qty": 1})],
    }])
    erp.call("sale.order", "action_confirm", [[so_id]])
    erp.deliver(so_id)
    try:
        table = check.invariants(erp)
        assert status_of(table, "invoicing") == "FAIL"
        assert "not invoiced" in evidence_of(table, "invoicing")

        # Invoicing but leaving the invoice in draft is still a failure.
        invoice_ids = erp.invoice(so_id)
        assert status_of(check.invariants(erp), "invoicing") == "FAIL"
        assert "draft" in evidence_of(check.invariants(erp), "invoicing")

        erp.post(invoice_ids)
        assert status_of(check.invariants(erp), "invoicing") == "pass"
    finally:
        pass


# -- agent-defined rules ------------------------------------------------------
def test_registered_rules_run_and_persist(erp):
    from lib import check
    from lib.check import Rule

    check.register(Rule("cap", "spend under 1e9", lambda c: (True, "spend 0 < 1e9")))
    check.register(Rule("impossible", "always fails", lambda c: (False, "by design")))

    table = check.all(erp)
    assert status_of(table, "cap") == "pass"
    assert status_of(table, "impossible") == "FAIL"
    # Still there on the next call — the registry is what finish() gates on.
    assert status_of(check.all(erp), "impossible") == "FAIL"
    assert {r.id for r in check.registered()} == {"cap", "impossible"}


def test_reregistering_an_id_replaces_it(erp):
    from lib import check
    from lib.check import Rule

    check.register(Rule("cap", "v1", lambda c: (False, "v1")))
    check.register(Rule("cap", "v2", lambda c: (True, "v2")))
    assert len(check.registered()) == 1
    assert status_of(check.all(erp), "cap") == "pass"


def test_a_raising_rule_fails_that_row_only(erp):
    from lib import check
    from lib.check import Rule

    def explode(_client):
        raise ValueError("bad domain")

    check.register(Rule("boom", "raises", explode))
    table = check.all(erp)
    assert status_of(table, "boom") == "FAIL"
    assert "ValueError" in evidence_of(table, "boom")
    assert status_of(table, "drafts") in ("pass", "FAIL")  # the others still ran


# -- the finish gate ----------------------------------------------------------
def test_finish_refuses_while_a_hard_check_fails_then_relents(erp, tmp_path):
    import os

    from lib import check, finish as finish_module
    from lib.check import Rule

    os.environ["ERP_SUMMARY_PATH"] = str(tmp_path / "summary.md")
    finish_module.SUMMARY_PATH = str(tmp_path / "summary.md")
    check.register(Rule("impossible", "always fails", lambda c: (False, "by design")))

    first = finish_module.finish("done?", client=erp)
    assert "finish refused (attempt 1/3)" in first
    assert finish_module.FINISH_SENTINEL not in first

    second = finish_module.finish("done?", client=erp)
    assert "finish refused (attempt 2/3)" in second

    third = finish_module.finish("giving up", client=erp)
    assert finish_module.FINISH_SENTINEL in third
    assert "still failing" in third
    assert (tmp_path / "summary.md").exists()
    assert "giving up" in (tmp_path / "summary.md").read_text()


def test_finish_passes_straight_through_when_checks_are_clean(erp, tmp_path):
    from lib import check, finish as finish_module

    if [r for r in check.invariants(erp).all() if r["status"] == "FAIL"]:
        pytest.skip("container is not in a clean state; run `make devbox` for a fresh one")

    finish_module.SUMMARY_PATH = str(tmp_path / "summary.md")
    result = finish_module.finish("all good", client=erp)
    assert finish_module.FINISH_SENTINEL in result
    assert "refused" not in result
    assert finish_module.refusals() == 0


def test_ungated_finish_never_refuses(erp, tmp_path):
    from lib import check, finish as finish_module
    from lib.check import Rule

    finish_module.SUMMARY_PATH = str(tmp_path / "summary.md")
    check.register(Rule("impossible", "always fails", lambda c: (False, "by design")))
    result = finish_module.finish("no gate", client=erp, gate=False)
    assert finish_module.FINISH_SENTINEL in result
    assert finish_module.refusals() == 0
