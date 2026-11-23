-- I09 check of 032: read-only catalog verification, runs as dwh_app. Expected: 7 results, every one PASS.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH t AS (SELECT c.oid, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'etl' AND c.relname IN ('object_schedule', 'object_schedule_event') AND c.relkind = 'r'),
f AS (SELECT p.oid, p.proname, p.prosecdef, p.proconfig, p.proacl, p.proowner
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
       WHERE (n.nspname, p.proname) IN (('etl', 'object_schedule_next'), ('etl', 'object_schedule_ready'),
                                        ('app', 'object_schedule_save'), ('app', 'object_schedule_list'),
                                        ('etl', 'object_schedule_fire'))),
pub AS (SELECT f.proname FROM f, aclexplode(coalesce(f.proacl, acldefault('f', f.proowner))) x WHERE x.grantee = 0),
q AS (SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
       WHERE n.nspname = 'etl' AND c.relname = 'object_run_request'),
checks AS (
  SELECT 1 AS n, 'tables etl.object_schedule and etl.object_schedule_event present' AS check_name,
    (SELECT count(*) FROM t) = 2 AS ok
  UNION ALL SELECT 2, 'run queue has trigger_kind, schedule_id, attempt, retry_of_request_id',
    (SELECT count(*) FROM pg_attribute a, q WHERE a.attrelid = q.oid AND NOT a.attisdropped
      AND a.attname IN ('trigger_kind', 'schedule_id', 'attempt', 'retry_of_request_id')) = 4
  UNION ALL SELECT 3, '5 functions of 032 present; save, list, fire are SECURITY DEFINER with search_path',
    (SELECT count(*) FROM f) = 5
    AND (SELECT count(*) FROM f WHERE proname IN ('object_schedule_save', 'object_schedule_list', 'object_schedule_fire')
          AND prosecdef AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%')) = 3
  UNION ALL SELECT 4, 'no EXECUTE for PUBLIC', NOT EXISTS (SELECT 1 FROM pub)
  UNION ALL SELECT 5, 'dwh_app executes save and list only',
    (SELECT count(*) FROM f WHERE has_function_privilege('dwh_app', oid, 'EXECUTE')) = 2
    AND (SELECT count(*) FROM f WHERE proname IN ('object_schedule_save', 'object_schedule_list')
          AND has_function_privilege('dwh_app', oid, 'EXECUTE')) = 2
  UNION ALL SELECT 6, 'dwh_etl executes fire only',
    (SELECT count(*) FROM f WHERE has_function_privilege('dwh_etl', oid, 'EXECUTE')) = 1
    AND (SELECT count(*) FROM f WHERE proname = 'object_schedule_fire' AND has_function_privilege('dwh_etl', oid, 'EXECUTE')) = 1
  UNION ALL SELECT 7, 'dwh_app and dwh_etl have no rights on the schedule tables',
    NOT EXISTS (SELECT 1 FROM t, unnest(ARRAY['dwh_app', 'dwh_etl']) r, unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE']) p
                 WHERE has_table_privilege(r, t.oid, p))
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
