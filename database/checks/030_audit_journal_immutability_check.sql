-- I13-14 check of 030: read-only catalog verification, runs as dwh_app. Expected: 6 results, every one PASS.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

-- the table and the function are found through pg_class / pg_proc (dwh_app has no rights on them)
WITH t AS (SELECT c.oid, c.relowner FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'audit' AND c.relname = 'security_event'),
tg AS (SELECT tg.tgname, tg.tgenabled, tg.tgtype FROM pg_trigger tg, t WHERE tg.tgrelid = t.oid AND NOT tg.tgisinternal),
f AS (SELECT p.oid, p.proacl, p.proowner FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
       WHERE n.nspname = 'audit' AND p.proname = 'reject_security_event_truncate'),
writers AS (SELECT r.rolname FROM pg_roles r, t
             WHERE r.rolname !~ '^pg_' AND NOT r.rolsuper AND r.oid <> t.relowner
               AND (has_table_privilege(r.oid, t.oid, 'UPDATE') OR has_table_privilege(r.oid, t.oid, 'DELETE')
                    OR has_table_privilege(r.oid, t.oid, 'TRUNCATE'))),
checks AS (
  SELECT 1 AS n, 'TRUNCATE guard trigger present and enabled' AS check_name,
    EXISTS (SELECT 1 FROM tg WHERE tgname = 'trg_security_event_no_truncate' AND tgenabled <> 'D') AS ok
  UNION ALL SELECT 2, 'row guard trg_security_event_append_only still present and enabled',
    EXISTS (SELECT 1 FROM tg WHERE tgname = 'trg_security_event_append_only' AND tgenabled <> 'D')
  UNION ALL SELECT 3, 'dwh_etl cannot UPDATE, DELETE or TRUNCATE the audit journal',
    (SELECT NOT (has_table_privilege('dwh_etl', t.oid, 'UPDATE') OR has_table_privilege('dwh_etl', t.oid, 'DELETE')
                 OR has_table_privilege('dwh_etl', t.oid, 'TRUNCATE')) FROM t)
  UNION ALL SELECT 4, 'dwh_etl still INSERTs and SELECTs (loads unchanged)',
    (SELECT has_table_privilege('dwh_etl', t.oid, 'INSERT') AND has_table_privilege('dwh_etl', t.oid, 'SELECT') FROM t)
  UNION ALL SELECT 5, 'no role except the owner and superusers can change the audit journal',
    NOT EXISTS (SELECT 1 FROM writers)
  UNION ALL SELECT 6, 'guard function: present, no EXECUTE for PUBLIC',
    EXISTS (SELECT 1 FROM f) AND NOT EXISTS (SELECT 1 FROM f, aclexplode(coalesce(f.proacl, acldefault('f', f.proowner))) x
                                              WHERE x.grantee = 0)
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
