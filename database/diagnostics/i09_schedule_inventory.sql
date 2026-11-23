-- I09 step 1: what the scheduler can build on (read only; structure and counts, no business data).
-- Run on the server as postgres in a read-only transaction:
--   sudo runuser -u postgres -- psql -X -d dwh -v ON_ERROR_STOP=1 < /srv/dwh/incoming/i09_schedule_inventory.sql
SET default_transaction_read_only = on;
BEGIN;
SHOW transaction_read_only;

\echo '== 1. time zone of the database server and of the session; current time'
SELECT current_setting('TimeZone') AS session_tz,
       (SELECT setting FROM pg_settings WHERE name = 'log_timezone') AS log_tz,
       now() AS now_db, now() AT TIME ZONE 'Asia/Baku' AS now_baku;

\echo '== 2. tables, views and functions whose name mentions schedule / cron / calendar (old CRM mechanism included)'
SELECT 'table' AS kind, n.nspname || '.' || c.relname AS name, c.relkind::text AS detail
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind IN ('r', 'v', 'm', 'p') AND n.nspname NOT IN ('pg_catalog', 'information_schema')
   AND c.relname ~* '(schedul|cron|calendar|timer|trigger_plan)'
UNION ALL
SELECT 'function', n.nspname || '.' || p.proname, pg_get_function_identity_arguments(p.oid)
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') AND p.proname ~* '(schedul|cron|calendar)'
ORDER BY 1, 2;

\echo '== 3. columns of the object run queue (etl.object_run_request) and its checks'
SELECT column_name, data_type, is_nullable, column_default
  FROM information_schema.columns WHERE table_schema = 'etl' AND table_name = 'object_run_request'
 ORDER BY ordinal_position;
SELECT conname, pg_get_constraintdef(oid) AS definition FROM pg_constraint
 WHERE conrelid = 'etl.object_run_request'::regclass ORDER BY 1;

\echo '== 4. requests of the object queue by kind, status and result (counts only)'
SELECT run_kind, status, coalesce(result_code, '-') AS result_code, count(*) AS requests,
       min(requested_at)::date AS first_at, max(requested_at)::date AS last_at
  FROM etl.object_run_request GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo '== 5. objects that can be scheduled: active, with a published STG version (counts per source)'
SELECT s.code AS source, count(*) AS objects,
       count(*) FILTER (WHERE o.is_active) AS active,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM meta.object_config_current cc
                                       WHERE cc.source_object_id = o.source_object_id)) AS with_config
  FROM meta.source_object o JOIN meta.source_system s ON s.source_system_id = o.source_system_id
 GROUP BY 1 ORDER BY 1;

\echo '== 6. duration of finished object requests (minutes): to choose a safe minimal interval'
SELECT count(*) AS finished, round(avg(extract(epoch FROM finished_at - started_at)) / 60, 1) AS avg_min,
       round(max(extract(epoch FROM finished_at - started_at)) / 60, 1) AS max_min
  FROM etl.object_run_request WHERE started_at IS NOT NULL AND finished_at IS NOT NULL;

\echo '== 7. functions of the object queue and who may execute them'
SELECT n.nspname || '.' || p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')' AS function,
       p.prosecdef AS security_definer,
       has_function_privilege('dwh_app', p.oid, 'EXECUTE') AS dwh_app,
       has_function_privilege('dwh_etl', p.oid, 'EXECUTE') AS dwh_etl
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE (n.nspname, p.proname) IN (('etl', 'object_run_request_claim'), ('etl', 'object_run_request_start'),
                                  ('etl', 'object_run_request_finish'), ('app', 'integration_object_run_request_create'),
                                  ('app', 'integration_object_run_request_cancel'))
 ORDER BY 1;

ROLLBACK;
\echo '== I09_INVENTORY_DONE'
