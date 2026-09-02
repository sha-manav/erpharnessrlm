"""Read-only SQL against the task's Odoo database (PLAN.md P3.1).

Some questions are one join away in SQL and a dozen RPC round trips away through the ORM —
"which confirmed order lines have no purchase line for the same product", say. This exposes
that without exposing a way to corrupt the database.

The guarantee is enforced by **Postgres**, not by string matching: a `ro_agent` role is
created with `SELECT`-only grants and `db.sql` connects as that role, so a write is
rejected by the server even if it slips past the prefix check. The prefix check exists to
give a readable error before the round trip, not as the safety mechanism.
"""

from __future__ import annotations

import os
import re

from .fmt import Table

EXPORTS = ["db"]

DEFAULT_LIMIT = 200
RO_USER = "ro_agent"
RO_PASSWORD = "ro_agent_readonly"
ALLOWED_FIRST_WORD = ("select", "with", "explain", "table")


class SqlRefused(RuntimeError):
    """A statement that is not a read."""


def _connect(user: str, password: str, dbname: str | None = None):
    import psycopg2  # present in the task image (Odoo depends on it)

    return psycopg2.connect(
        host=os.environ.get("PGHOST", "127.0.0.1"),
        port=int(os.environ.get("PGPORT", "5432")),
        user=user,
        password=password,
        dbname=dbname or os.environ.get("PGDATABASE", "bench"),
        connect_timeout=10,
    )


class Db:
    def __init__(self, dbname: str | None = None):
        self.dbname = dbname or os.environ.get("PGDATABASE", "bench")
        self._ready = False

    def _ensure_role(self) -> None:
        """Create the read-only role once per kernel, idempotently."""
        if self._ready:
            return
        admin = _connect(os.environ.get("PGUSER", "odoo"),
                         os.environ.get("PGPASSWORD", "odoo"), self.dbname)
        admin.autocommit = True
        try:
            with admin.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM pg_roles WHERE rolname = %s", (RO_USER,))
                if not cur.fetchone():
                    cur.execute(
                        f'CREATE ROLE {RO_USER} LOGIN PASSWORD %s', (RO_PASSWORD,))
                cur.execute(f'GRANT CONNECT ON DATABASE "{self.dbname}" TO {RO_USER}')
                cur.execute(f"GRANT USAGE ON SCHEMA public TO {RO_USER}")
                cur.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {RO_USER}")
                cur.execute(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    f"GRANT SELECT ON TABLES TO {RO_USER}")
        finally:
            admin.close()
        self._ready = True

    def sql(self, query: str, limit: int = DEFAULT_LIMIT) -> Table:
        """Run a read-only query and return a `Table`.

        Adds `LIMIT` when the query has none, so a stray `select * from stock_move` costs
        a screenful rather than the whole trajectory's context budget.
        """
        text = query.strip().rstrip(";")
        first = re.sub(r"^\(*\s*", "", text).split(None, 1)[0].lower() if text else ""
        if first not in ALLOWED_FIRST_WORD:
            raise SqlRefused(
                f"db.sql runs reads only; statement starts with {first!r}. "
                "Use the erp helpers to change anything.")
        if limit and not re.search(r"\blimit\b\s+\d+\s*$", text, re.I):
            text = f"{text} LIMIT {int(limit)}"

        self._ensure_role()
        connection = _connect(RO_USER, RO_PASSWORD, self.dbname)
        try:
            with connection.cursor() as cur:
                cur.execute(text)
                columns = [d[0] for d in cur.description] if cur.description else []
                rows = [dict(zip(columns, row)) for row in cur.fetchall()]
        finally:
            connection.close()
        return Table(rows, columns, f"sql ({len(rows)} rows)")

    def tables(self, like: str | None = None) -> Table:
        """What tables exist — the SQL counterpart to `erp.fields`."""
        query = ("SELECT table_name FROM information_schema.tables "
                 "WHERE table_schema = 'public'")
        if like:
            query += f" AND table_name LIKE '%{re.sub(r'[^a-zA-Z0-9_]', '', like)}%'"
        return self.sql(query + " ORDER BY table_name", limit=500)

    def columns(self, table: str) -> Table:
        safe = re.sub(r"[^a-zA-Z0-9_]", "", table)
        return self.sql(
            "SELECT column_name, data_type FROM information_schema.columns "
            f"WHERE table_schema = 'public' AND table_name = '{safe}' "
            "ORDER BY ordinal_position", limit=300)


db = Db()
