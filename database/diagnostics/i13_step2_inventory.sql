-- I13 (I13-01..I13-04), step 2: read-only inventory of the DWH server. Run as dwh_app:
--   sudo /opt/dwh/current/venv/bin/python /opt/dwh/current/src/tools/server/dwh_readonly_sql.py /srv/dwh/incoming/i13_step2_inventory.sql
-- Catalog only (pg_proc, pg_class) and the status view of the loads. No business data, no values.

-- A. Functions of app/etl/meta that dwh_app or dwh_etl may call: security definer,
--    whether the text checks a session, writes the audit journal, changes rows.
SELECT n.nspname AS schema, p.proname AS function, pg_get_function_identity_arguments(p.oid) AS args,
       concat_ws(',', CASE WHEN has_function_privilege('dwh_app', p.oid, 'EXECUTE') THEN 'dwh_app' END,
                      CASE WHEN has_function_privilege('dwh_etl', p.oid, 'EXECUTE') THEN 'dwh_etl' END) AS callers,
       p.prosecdef AS secdef,
       (p.prosrc ~* '_session_user|auth_session') AS session_check,
       (p.prosrc ~* 'log_security_event') AS audit,
       (p.prosrc ~* '\m(insert|update|delete)\M') AS writes
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname IN ('app', 'etl', 'meta')
   AND (has_function_privilege('dwh_app', p.oid, 'EXECUTE') OR has_function_privilege('dwh_etl', p.oid, 'EXECUTE'))
 ORDER BY 1, 2, 3;

-- B. Totals of part A: callers, security definer, with a session check, with audit, writing.
SELECT concat_ws(',', CASE WHEN has_function_privilege('dwh_app', p.oid, 'EXECUTE') THEN 'dwh_app' END,
                      CASE WHEN has_function_privilege('dwh_etl', p.oid, 'EXECUTE') THEN 'dwh_etl' END) AS callers,
       count(*) AS functions,
       count(*) FILTER (WHERE p.prosecdef) AS secdef,
       count(*) FILTER (WHERE p.prosrc ~* '_session_user|auth_session') AS session_check,
       count(*) FILTER (WHERE p.prosrc ~* 'log_security_event') AS audit,
       count(*) FILTER (WHERE p.prosrc ~* '\m(insert|update|delete)\M') AS writes,
       count(*) FILTER (WHERE p.prosrc ~* '\m(insert|update|delete)\M' AND p.prosrc !~* 'log_security_event') AS writes_without_audit
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname IN ('app', 'etl', 'meta')
   AND (has_function_privilege('dwh_app', p.oid, 'EXECUTE') OR has_function_privilege('dwh_etl', p.oid, 'EXECUTE'))
 GROUP BY 1 ORDER BY 1;

-- C. Tables that dwh_app or dwh_etl may change directly (not through functions).
SELECT n.nspname AS schema, c.relname AS table_name,
       concat_ws(',', CASE WHEN has_table_privilege('dwh_app', c.oid, 'INSERT,UPDATE,DELETE') THEN 'dwh_app' END,
                      CASE WHEN has_table_privilege('dwh_etl', c.oid, 'INSERT,UPDATE,DELETE') THEN 'dwh_etl' END) AS writers
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind IN ('r', 'p') AND n.nspname IN ('app', 'etl', 'meta', 'audit', 'secret_store', 'dq')
   AND (has_table_privilege('dwh_app', c.oid, 'INSERT,UPDATE,DELETE') OR has_table_privilege('dwh_etl', c.oid, 'INSERT,UPDATE,DELETE'))
 ORDER BY 1, 2;

-- D. Loads that look stuck: a run or request still running more than an hour after its start.
SELECT source_system_code, source_schema, source_object, stg_run_status, stg_started_at, ods_run_status, ods_started_at,
       last_request_id, last_request_status, last_request_at
  FROM etl.v_object_load_status
 WHERE (stg_run_status = 'RUNNING' AND stg_started_at < now() - interval '1 hour')
    OR (ods_run_status = 'RUNNING' AND ods_started_at < now() - interval '1 hour')
    OR (has_active_request AND last_request_at < now() - interval '1 hour');

-- E. Last result of every object: error codes only (which codes occur, how often).
SELECT coalesce(stg_error_code, '-') AS stg_error_code, coalesce(ods_error_code, '-') AS ods_error_code,
       coalesce(last_request_result, '-') AS last_request_result, count(*) AS objects
  FROM etl.v_object_load_status
 WHERE is_active
 GROUP BY 1, 2, 3 ORDER BY 4 DESC;

-- F. The dq schema: tables and whether anything is left in them (the old DQ was removed in I18).
SELECT c.relname AS dq_table, c.reltuples::bigint AS estimated_rows
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'dq' AND c.relkind = 'r'
 ORDER BY 1
