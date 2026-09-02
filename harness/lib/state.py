"""Database snapshots and the dry-run protocol (PLAN.md P3.2).

Why this exists, from the measurement: P1.4 found 17 of 23 stock-harness failures were
plans whose dates did not work. `check.timeline_feasible` can now *see* that — but seeing
it after the orders are confirmed is worth much less than seeing it before. A snapshot
gives the agent somewhere to be wrong: rehearse the plan on a clone, run `check.all()`
against the clone, fix, and only then touch the real database.

`snapshot(name)` uses `CREATE DATABASE ... TEMPLATE ...`, which needs no other session
connected to the source — hence the `pg_terminate_backend` sweep. Odoo reconnects on its
next request, and `odoo.conf` sets no `dbfilter`, so the clone is reachable over RPC as
`erp.on(name)` (both verified on this image).

    def plan(client):                      # look records up by name, never by id
        ...
    state.snapshot("s1")
    plan(erp.on("s1")); check.all(erp.on("s1"))     # fix and repeat until clean
    plan(erp);          check.all()                 # then, once, for real
    state.diff("start", ODOO_DB)                    # and confirm it matches
"""

from __future__ import annotations

import os
import re

from .fmt import Table

EXPORTS = ["state"]

# The models whose movement constitutes "what this task changed".
DIFF_MODELS = [
    ("sale_order", "sale.order"),
    ("sale_order_line", "sale.order.line"),
    ("purchase_order", "purchase.order"),
    ("purchase_order_line", "purchase.order.line"),
    ("mrp_production", "mrp.production"),
    ("stock_picking", "stock.picking"),
    ("stock_move", "stock.move"),
    ("account_move", "account.move"),
]


def _admin(dbname: str = "postgres"):
    import psycopg2

    connection = psycopg2.connect(
        host=os.environ.get("PGHOST", "127.0.0.1"),
        port=int(os.environ.get("PGPORT", "5432")),
        user=os.environ.get("PGUSER", "odoo"),
        password=os.environ.get("PGPASSWORD", "odoo"),
        dbname=dbname,
        connect_timeout=10,
    )
    connection.autocommit = True            # CREATE/DROP DATABASE cannot run in a transaction
    return connection


def _safe(name: str) -> str:
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,48}", name):
        raise ValueError(
            f"snapshot name {name!r} must be letters, digits and underscores, "
            "starting with a letter")
    return name


class State:
    def __init__(self, source: str | None = None):
        self.source = source or os.environ.get("ODOO_DB", "bench")

    def snapshot(self, name: str) -> str:
        """Clone the working database under `name`, replacing any existing clone."""
        _safe(name)
        connection = _admin()
        try:
            with connection.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
                # TEMPLATE requires no other session on the source; Odoo holds a pool open.
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()", (self.source,))
                cur.execute(f'CREATE DATABASE "{name}" TEMPLATE "{self.source}"')
        finally:
            connection.close()
        return name

    def drop(self, name: str) -> str:
        _safe(name)
        connection = _admin()
        try:
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
                cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
        finally:
            connection.close()
        return name

    def list(self) -> Table:
        connection = _admin()
        try:
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT datname FROM pg_database WHERE datistemplate = false "
                    "ORDER BY datname")
                rows = [{"database": r[0], "is_source": r[0] == self.source}
                        for r in cur.fetchall()]
        finally:
            connection.close()
        return Table(rows, ["database", "is_source"], "databases")

    def _counts(self, dbname: str) -> dict:
        connection = _admin(dbname)
        counts: dict[str, dict] = {}
        try:
            with connection.cursor() as cur:
                for table, model in DIFF_MODELS:
                    try:
                        cur.execute(f"SELECT count(*) FROM {table}")
                        total = cur.fetchone()[0]
                        cur.execute(
                            f"SELECT count(*) FROM {table} "
                            "WHERE write_date IS DISTINCT FROM create_date")
                        changed = cur.fetchone()[0]
                    except Exception:       # noqa: BLE001 - a missing table is not a failure
                        connection.rollback()
                        continue
                    counts[model] = {"rows": total, "modified": changed}
                try:
                    cur.execute(
                        "SELECT coalesce(sum(amount_total), 0) FROM purchase_order "
                        "WHERE state IN ('purchase', 'done')")
                    counts["_cash_committed"] = {"rows": float(cur.fetchone()[0]), "modified": 0}
                    cur.execute("SELECT count(*) FROM account_move WHERE state = 'posted'")
                    counts["_invoices_posted"] = {"rows": cur.fetchone()[0], "modified": 0}
                except Exception:           # noqa: BLE001
                    connection.rollback()
        finally:
            connection.close()
        return counts

    def diff(self, a: str = "start", b: str | None = None) -> Table:
        """What changed between two databases, per model.

        Used two ways: to see what a rehearsal did, and to confirm the real run did the
        same thing. A mismatch means the plan was not reproducible — usually an id captured
        during the rehearsal that means something else on main.
        """
        b = b or self.source
        left, right = self._counts(a), self._counts(b)
        rows = []
        for model in sorted(set(left) | set(right)):
            before = left.get(model, {"rows": 0, "modified": 0})
            after = right.get(model, {"rows": 0, "modified": 0})
            created = after["rows"] - before["rows"]
            changed = after["modified"] - before["modified"]
            if created or changed:
                rows.append({
                    "model": model.lstrip("_"),
                    "created": round(created, 2) if isinstance(created, float) else created,
                    "changed": changed,
                })
        title = f"diff {a} -> {b}"
        return Table(rows, ["model", "created", "changed"], title) if rows else Table(
            [], ["model", "created", "changed"], f"{title} (no differences)")


    def rehearse(self, plan_fn, name: str = "rehearsal"):
        """Run `plan_fn(client)` on a throwaway clone and return `check.all()` for it.

        Four steps the contract asked the agent to orchestrate by hand -- snapshot, run,
        check, drop -- in one call, so a refused plan costs one step to find and one to
        fix instead of six. The plan function must look records up by name or domain, never
        by ids: the clone numbers its own records.
        """
        from .erp import erp
        from . import check as check_module

        self.snapshot(name)
        client = erp.on(name)
        try:
            plan_fn(client)
            return check_module.all(client)
        finally:
            try:
                self.drop(name)
            except Exception:   # noqa: BLE001 - a leftover clone is not worth failing over
                pass


state = State()
