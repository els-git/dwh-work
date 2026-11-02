# I02-04 M-SS01: read-only check of secret_store in the working dwh.
# Uses the project launcher's .env loader, get_api_config() and connect_readonly()
# (role dwh_app); stops unless SHOW transaction_read_only = on; always rollback.
# Prints only catalog checks and row counts; no secrets, no row contents.
# Run in Windows PowerShell 5.1: powershell -ExecutionPolicy Bypass -File <this file>
$ErrorActionPreference = 'Stop'
Set-Location 'C:\Users\elshad.asgarov\Desktop\DWH\dwh'
$python = 'C:\Python312\python.exe'
@'
import importlib.util, os, sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("launcher", Path("tools/run_local_dwh_threaded.py").resolve())
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
launcher.load_env()
launcher.configure_python()

from dwh_api.config import get_api_config
from dwh_api.db import connect_readonly

SQL = r'''
-- I02-04 M-SS01 check: read-only catalog verification of secret_store.
-- Plain SQL (no psql meta-commands): runs through connect_readonly() as dwh_app
-- or through psql. SELECT only; reads pg_catalog, never row contents.
-- Expected when present: every result = PASS. When absent: 'schema exists' = FAIL
-- and the other checks FAIL as well.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name,
       current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH s AS (SELECT oid, nspowner, nspacl FROM pg_namespace WHERE nspname = 'secret_store'),
t AS (SELECT c.oid, c.relname FROM pg_class c JOIN s ON c.relnamespace = s.oid WHERE c.relkind = 'r'),
f AS (SELECT p.oid, p.prosecdef FROM pg_proc p JOIN s ON p.pronamespace = s.oid),
checks AS (
  SELECT 1 AS n, 'schema exists' AS check_name, (SELECT count(*) FROM s) = 1 AS ok
  UNION ALL SELECT 2, 'tables binding, operation_event',
    coalesce((SELECT array_agg(relname ORDER BY relname) FROM t) = ARRAY['binding', 'operation_event']::name[], false)
  UNION ALL SELECT 3, 'binding constraints = 6',
    (SELECT count(*) FROM pg_constraint WHERE conrelid = (SELECT oid FROM t WHERE relname = 'binding')) = 6
  UNION ALL SELECT 4, 'operation_event constraints = 6',
    (SELECT count(*) FROM pg_constraint WHERE conrelid = (SELECT oid FROM t WHERE relname = 'operation_event')) = 6
  UNION ALL SELECT 5, 'named constraints present',
    (SELECT count(*) FROM pg_constraint WHERE connamespace = (SELECT oid FROM s) AND conname IN (
      'ciphertext_digest_format', 'binding_state_timestamps', 'operation_code_by_phase',
      'operation_once_per_phase', 'event_for_create_operation')) = 5
  UNION ALL SELECT 6, 'indexes present',
    (SELECT count(*) FROM pg_class WHERE relnamespace = (SELECT oid FROM s) AND relkind = 'i'
       AND relname IN ('binding_source_state_idx', 'operation_event_source_created_idx')) = 2
  UNION ALL SELECT 7, '4 guard triggers enabled',
    (SELECT count(*) FROM pg_trigger WHERE tgrelid IN (SELECT oid FROM t) AND NOT tgisinternal AND tgenabled = 'O'
       AND tgname IN ('binding_guard', 'event_append_only', 'event_insert_guard', 'binding_insert_guard')) = 4
  UNION ALL SELECT 8, '4 functions, none SECURITY DEFINER',
    (SELECT count(*) FROM f) = 4 AND NOT EXISTS (SELECT 1 FROM f WHERE prosecdef)
  UNION ALL SELECT 9, 'schema: no grants beyond owner',
    (SELECT count(*) FROM s) = 1 AND NOT EXISTS (
      SELECT 1 FROM s, aclexplode(coalesce(s.nspacl, acldefault('n', s.nspowner))) a WHERE a.grantee <> s.nspowner)
  UNION ALL SELECT 10, 'tables: no grants beyond owner',
    (SELECT count(*) FROM t) = 2 AND NOT EXISTS (
      SELECT 1 FROM t JOIN pg_class c ON c.oid = t.oid, aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a
       WHERE a.grantee <> c.relowner)
  UNION ALL SELECT 11, 'functions: no PUBLIC or other EXECUTE',
    (SELECT count(*) FROM f) = 4 AND NOT EXISTS (
      SELECT 1 FROM f JOIN pg_proc p ON p.oid = f.oid, aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
       WHERE a.grantee <> p.proowner)
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;

-- Row counts only (no contents). NULL = table absent or no SELECT privilege
-- for the current role (expected for dwh_app: no grants by design).
SELECT r.relname AS table_name,
       CASE WHEN c.oid IS NOT NULL THEN
         CASE WHEN has_schema_privilege(c.relnamespace, 'USAGE') AND has_table_privilege(c.oid, 'SELECT') THEN
           (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM secret_store.%I', r.relname), false, true, '')))[1]::text::bigint
         END
       END AS row_count
  FROM (VALUES ('binding'), ('operation_event')) AS r(relname)
  LEFT JOIN pg_class c
    ON c.relname = r.relname AND c.relkind = 'r'
   AND c.relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'secret_store');
'''

def statements(text):
    code = "\n".join(l for l in text.splitlines() if not l.strip().startswith("--"))
    for chunk in code.split(";"):
        if chunk.strip():
            yield chunk

conn = connect_readonly(get_api_config())
try:
    with conn.cursor() as cur:
        cur.execute("SHOW transaction_read_only")
        ro = list(cur.fetchone().values())[0]
        print("SHOW transaction_read_only:", ro)
        if ro != "on":
            raise SystemExit("STOP: connection is not read-only")
        for st in statements(SQL):
            cur.execute(st)
            for row in cur.fetchall():
                print(" | ".join("" if v is None else str(v) for v in row.values()))
finally:
    conn.rollback()
    conn.close()
print("CHECK_SCRIPT_FINISHED")
'@ | & $python -
if ($LASTEXITCODE -ne 0) { throw "CHECK FAILED: python exit code $LASTEXITCODE" }
