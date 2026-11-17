-- I18 step 1b: dependencies for the deletion order of package 020 (catalog only; Q4 of step 1 compared
-- regclass text, which drops the schema for schemas on the search path, and found nothing).
-- Run on the server: dwh_readonly_sql.py (dwh_app, read-only transaction, rollback).
-- R1 every foreign key between tables of meta, etl, dq, app, audit, stg, ods, dm (schema-qualified names)
SELECT 'R1' AS q, fn.nspname || '.' || fc.relname AS from_table, tn.nspname || '.' || tc.relname AS to_table,
       c.conname, pg_get_constraintdef(c.oid) AS definition
  FROM pg_constraint c
  JOIN pg_class fc ON fc.oid = c.conrelid JOIN pg_namespace fn ON fn.oid = fc.relnamespace
  JOIN pg_class tc ON tc.oid = c.confrelid JOIN pg_namespace tn ON tn.oid = tc.relnamespace
 WHERE c.contype = 'f' AND tn.nspname IN ('meta', 'etl', 'dq', 'app', 'audit', 'stg', 'ods', 'dm')
 ORDER BY 3, 2, 4;
-- R2 user triggers on tables of these schemas (a trigger may forbid deleting rows)
SELECT 'R2' AS q, n.nspname || '.' || c.relname AS table_name, t.tgname, t.tgfoid::regprocedure::text AS function,
       pg_get_triggerdef(t.oid) AS definition
  FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE NOT t.tgisinternal AND n.nspname IN ('meta', 'etl', 'dq', 'app', 'audit') ORDER BY 2, 3;
-- R3 tables and views of dq and etl and the legacy meta tables: kind, estimated rows
SELECT 'R3' AS q, n.nspname || '.' || c.relname AS relation, c.relkind, c.reltuples::bigint AS est_rows
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind IN ('r', 'v', 'm', 'p')
   AND (n.nspname IN ('dq', 'etl') OR (n.nspname = 'meta' AND c.relname ~ '(contract|mapping|integration|metadata|snapshot|watermark)'))
 ORDER BY 2;
-- R4 views and functions outside stg/ods/dm that read the legacy tables (they would break if the tables were dropped)
SELECT DISTINCT 'R4' AS q, sn.nspname || '.' || sc.relname AS legacy_table, dn.nspname || '.' || dc.relname AS dependent_view
  FROM pg_depend d JOIN pg_rewrite r ON r.oid = d.objid AND d.classid = 'pg_rewrite'::regclass
  JOIN pg_class dc ON dc.oid = r.ev_class JOIN pg_namespace dn ON dn.oid = dc.relnamespace
  JOIN pg_class sc ON sc.oid = d.refobjid JOIN pg_namespace sn ON sn.oid = sc.relnamespace
 WHERE dc.oid <> sc.oid AND sn.nspname IN ('meta', 'etl', 'dq')
   AND sc.relname IN ('column_contract', 'mapping_rule', 'batch_contract_snapshot', 'batch_mapping_snapshot', 'run_request',
                      'rule', 'run', 'result', 'issue', 'run_rule_snapshot', 'job', 'batch_run', 'job_run', 'error_log', 'watermark')
 ORDER BY 2, 3;
-- R5 functions of app, etl, meta, dq whose body uses the legacy tables or the old run queue
SELECT 'R5' AS q, p.oid::regprocedure::text AS function, p.prosecdef
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname IN ('app', 'etl', 'meta', 'dq')
   AND p.prosrc ~ '(column_contract|mapping_rule|run_request\M|dq\.(rule|run|result|issue)|batch_contract_snapshot|batch_mapping_snapshot)'
 ORDER BY 2;
-- R6 runs by pipeline (which batch runs belong to the old contour)
SELECT 'R6' AS q, CASE WHEN to_regclass('etl.batch_run') IS NULL THEN 'ABSENT' WHEN has_table_privilege('etl.batch_run', 'SELECT')
       THEN query_to_xml('SELECT pipeline_code, count(*) AS runs, min(batch_run_id) AS first_id, max(batch_run_id) AS last_id
                            FROM etl.batch_run GROUP BY 1 ORDER BY 1', false, true, '')::text ELSE 'NO_SELECT' END;
-- R7 columns of the tables that hold rows of the old contour
SELECT 'R7' AS q, table_schema || '.' || table_name AS table_name, string_agg(column_name || ' ' || data_type, ', ' ORDER BY ordinal_position) AS columns
  FROM information_schema.columns
 WHERE (table_schema, table_name) IN (('etl', 'batch_run'), ('etl', 'job_run'), ('etl', 'error_log'), ('etl', 'watermark'),
        ('etl', 'run_request'), ('dq', 'run'), ('dq', 'result'), ('dq', 'issue'), ('dq', 'rule'), ('dq', 'run_rule_snapshot'),
        ('meta', 'batch_contract_snapshot'), ('meta', 'batch_mapping_snapshot'), ('meta', 'column_contract'), ('meta', 'mapping_rule'),
        ('meta', 'ai_glossary'))
 GROUP BY 2 ORDER BY 2
