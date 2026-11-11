"""I13-07: source gateway (only reading statements, read-only session) and the watchdog
that no code opens a source connection around it. No real server."""

import re
import unittest
from pathlib import Path

from dwh_api import source_gateway as sg

ROOT = Path(__file__).resolve().parents[2]
SECRET = "synthetic-source-Ж-7"

READING = [
    "SELECT 1",
    "select current_setting('server_version') AS v",
    "  (SELECT a FROM t) UNION ALL (SELECT b FROM u)",
    "WITH x AS (SELECT id FROM app.t WHERE created >= %s) SELECT to_jsonb(a) FROM x a ORDER BY 1",
    "SHOW transaction_read_only",
    "DECLARE dwh_object_run NO SCROLL CURSOR FOR SELECT \"id\"::text FROM \"public\".\"t\" WHERE \"c\" >= CAST('x' AS bigint)",
    "FETCH FORWARD 5000 FROM dwh_object_run",
    "CLOSE dwh_object_run",
    "SELECT CASE c.relkind WHEN 'r' THEN 'TABLE' END FROM pg_catalog.pg_class c",
    "SELECT 'insert into t values (1); drop table t' AS harmless_literal",
    'SELECT "update", "delete" FROM t',
    "SELECT col_description(a.attrelid, a.attnum) AS column_comment FROM pg_attribute a",
    "SELECT 1;",
    "SELECT has_table_privilege(current_user, 'app_retail.application', 'INSERT')",
    "VALUES (1)",
]

WRITING = [
    "INSERT INTO t VALUES (1)",
    "update t set a = 1",
    "DELETE FROM t",
    "MERGE INTO t USING u ON true WHEN MATCHED THEN DELETE",
    "TRUNCATE t",
    "COPY t FROM STDIN",
    "COPY (SELECT 1) TO STDOUT",
    "CREATE TABLE t (a int)",
    "ALTER TABLE t ADD COLUMN b int",
    "DROP TABLE t",
    "GRANT SELECT ON t TO x",
    "CALL p()",
    "DO $$ BEGIN PERFORM 1; END $$",
    "LOCK TABLE t",
    "VACUUM t",
    "SET TRANSACTION READ WRITE",
    "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE",
    "BEGIN READ WRITE",
    "COMMIT",
    "RESET ALL",
    "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d",
    "SELECT * INTO new_t FROM t",
    "SELECT * FROM t FOR UPDATE",
    "SELECT * FROM t FOR SHARE",
    "SELECT 1; DELETE FROM t",
    "SELECT 1; SELECT 2",
    "SELECT set_config('default_transaction_read_only', 'off', false)",
    "SELECT nextval('s')",
    "SELECT pg_terminate_backend(1)",
    "SELECT pg_advisory_lock(1)",
    "SELECT dblink_exec('x', 'delete from t')",
    "SELECT lo_import('/etc/passwd')",
    "DECLARE c CURSOR FOR DELETE FROM t RETURNING *",
    "EXPLAIN ANALYZE DELETE FROM t",
    "",
    "   ",
    "-- only a comment",
]


class StatementTest(unittest.TestCase):
    def test_reading_statements_pass(self):
        for text in READING:
            with self.subTest(text=text):
                sg.check_statement(text)

    def test_writing_statements_are_blocked(self):
        for text in WRITING:
            with self.subTest(text=text):
                with self.assertRaises(sg.SourceGatewayError) as ctx:
                    sg.check_statement(text)
                self.assertEqual(ctx.exception.code, sg.SOURCE_WRITE_BLOCKED)
                self.assertNotIn(text.strip()[:12] or "x", ctx.exception.message if text.strip() else "y")


# ------------------------------------------------------------------ fake connection


class RawCursor:
    def __init__(self, raw):
        self.raw = raw
        self.row = None
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def execute(self, query, params=None):
        text = query if isinstance(query, str) else query.as_string(None)
        self.raw.sent.append(text)
        self.row = ("on" if self.raw.read_only_answer is None else self.raw.read_only_answer,) \
            if "transaction_read_only" in text else (1,)

    def fetchone(self):
        return self.row

    def fetchall(self):
        return [self.row]

    def fetchmany(self, size=None):
        return [self.row]

    def close(self):
        self.closed = True


class RawConn:
    connection = None  # psycopg sql.as_string() context: default encoding and adapters

    def __init__(self, read_only_answer=None):
        self.read_only_answer = read_only_answer
        self.sent = []
        self.read_only = False
        self.closed = False
        self.rollbacks = 0

    def cursor(self, **kw):
        return RawCursor(self)

    def rollback(self):
        self.rollbacks += 1

    def commit(self):
        pass

    def close(self):
        self.closed = True


def gateway(raw, seen=None):
    def connect_fn(**kwargs):
        if seen is not None:
            seen.update(kwargs)
        return raw
    return sg.connect(host="db", port=5432, dbname="src", user="reader", password=SECRET, statement_timeout_ms=1000,
                      connect_fn=connect_fn)


class ConnectTest(unittest.TestCase):
    def test_read_only_session_and_options(self):
        raw = RawConn()
        seen = {}
        conn = gateway(raw, seen)
        self.assertIn("default_transaction_read_only=on", seen["options"])
        self.assertIn("statement_timeout=1000", seen["options"])
        self.assertNotIn("lock_timeout", seen["options"])
        self.assertNotIn("keepalives", seen)
        self.assertTrue(raw.read_only)
        self.assertEqual(raw.sent, ["SHOW transaction_read_only"])
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            self.assertEqual(cur.fetchone(), (1,))
        conn.close()
        self.assertTrue(raw.closed)

    def test_lock_timeout_keepalives_and_cancel(self):
        raw = RawConn()
        seen = {}
        conn = sg.connect(host="db", port=5432, dbname="src", user="reader", password=SECRET, lock_timeout_ms=60000,
                          keepalives=True, connect_fn=lambda **kw: (seen.update(kw), raw)[1])
        self.assertIn("lock_timeout=60000", seen["options"])
        self.assertEqual((seen["keepalives"], seen["keepalives_idle"]), (1, 60))
        cancelled = []
        raw.cancel_safe = lambda timeout: cancelled.append(timeout)
        conn.cancel()
        self.assertEqual(cancelled, [10.0])
        self.assertEqual(raw.sent, ["SHOW transaction_read_only"])

    def test_not_read_only_fails_closed_before_any_query(self):
        raw = RawConn(read_only_answer="off")
        with self.assertRaises(sg.SourceGatewayError) as ctx:
            gateway(raw)
        self.assertEqual(ctx.exception.code, sg.SOURCE_NOT_READ_ONLY)
        self.assertEqual(raw.sent, ["SHOW transaction_read_only"])
        self.assertTrue(raw.closed)

    def test_write_never_reaches_the_source(self):
        raw = RawConn()
        conn = gateway(raw)
        from psycopg import sql
        for query in ("DELETE FROM t", sql.SQL("UPDATE {} SET a = 1").format(sql.Identifier("t"))):
            with self.assertRaises(sg.SourceGatewayError):
                conn.cursor().execute(query)
            with self.assertRaises(sg.SourceGatewayError):
                conn.execute(query)
        self.assertEqual(raw.sent, ["SHOW transaction_read_only"])

    def test_no_raw_connection_api(self):
        conn = gateway(RawConn())
        for name in ("pgconn", "cursor_factory", "autocommit", "set_read_only", "copy"):
            self.assertFalse(hasattr(conn, name), name)
        cur = conn.cursor()
        for name in ("executemany", "copy", "stream"):
            self.assertFalse(hasattr(cur, name), name)


# ------------------------------------------------------------------ watchdog

# Files allowed to open database connections directly, and why. Everything that
# reaches a SOURCE must go through dwh_api/source_gateway.py.
ALLOWED_DIRECT = {
    "backend/src/dwh_api/source_gateway.py": "the gateway itself",
    "backend/src/dwh_api/db.py": "DWH database (API roles)",
    "backend/src/dwh_api/secret_admin_executor.py": "DWH database (operator of SecretStore)",
    "backend/src/dwh_api/postgres_connector.py": "passes psycopg.connect to the gateway as connect_fn",
    "etl/src/dwh/db.py": "connect(): DWH database; connect_readonly(): source through the gateway",
}

DIRECT = re.compile(
    r"psycopg2?\s*\.\s*connect\b|from\s+psycopg2?\s+import[^\n]*\bconnect\b|psycopg\.Connection\s*\.\s*connect\b"
    r"|^\s*(import|from)\s+(requests|httpx|aiohttp|urllib3|pycurl)\b|urllib\.request|http\.client|\bsocket\s*\.\s*(socket|create_connection)\b"
    r"|\bpyodbc\b|\bcx_Oracle\b|\boracledb\b|\bpymysql\b|\bsqlalchemy\b|\bftplib\b|\bparamiko\b",
    re.MULTILINE,
)


class WatchdogTest(unittest.TestCase):
    def sources(self):
        for base in ("backend/src", "etl/src"):
            for path in sorted((ROOT / base).rglob("*.py")):
                yield path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8")

    def test_no_direct_connection_outside_allowed_files(self):
        found = [(name, m.group(0)) for name, text in self.sources() if name not in ALLOWED_DIRECT
                 for m in DIRECT.finditer(text)]
        self.assertEqual(found, [], "source connections must go through dwh_api/source_gateway.py")

    def test_crm_config_only_with_gateway(self):
        bad = re.compile(r"(?<!_readonly)\bconnect\s*\(\s*(get_crm_config\s*\(\s*\)|crm_config)\b")
        found = [name for name, text in self.sources() if bad.search(text)]
        self.assertEqual(found, [])

    def test_source_paths_use_gateway(self):
        texts = dict(self.sources())
        self.assertIn("source_gateway.connect(", texts["backend/src/dwh_api/postgres_connector.py"])
        self.assertIn("source_gateway.connect(", texts["etl/src/run_object.py"])
        self.assertIn("from dwh_api.source_gateway import connect", texts["etl/src/dwh/db.py"])


if __name__ == "__main__":
    unittest.main()
