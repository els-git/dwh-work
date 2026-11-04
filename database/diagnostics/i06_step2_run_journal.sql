-- I06 step 2: run journal structure (etl.batch_run, etl.job_run, etl.job, etl.error_log,
-- history views of the UI) and privileges of dwh_etl / dwh_app. Read-only, catalog
-- metadata only; from etl.job only configuration columns of OPERATION_COLLECTOR jobs;
-- from run tables only row counts. Every relation is found by name in pg_class
-- (no regclass casts: a missing object gives no row instead of an error).
SELECT 'role | ' || current_user || ' | db=' || current_database() || ' | ro=' || current_setting('transaction_read_only');

-- Columns of the journal tables and views.
SELECT 'column | ' || c.relname || ' | ' || a.attnum || ' | ' || a.attname || ' | ' || format_type(a.atttypid, a.atttypmod)
    || ' | not_null=' || a.attnotnull::text
    || coalesce(' | default=' || pg_get_expr(d.adbin, d.adrelid), '')
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
  LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
 WHERE n.nspname = 'etl' AND c.relname IN ('batch_run', 'job_run', 'job', 'error_log',
       'v_integration_run_history', 'v_integration_error')
 ORDER BY c.relname, a.attnum;

-- Constraints (checks, keys, references) of the journal tables.
SELECT 'constraint | ' || c.relname || ' | ' || k.conname || ' | ' || k.contype::text || ' | ' || pg_get_constraintdef(k.oid)
  FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'etl' AND c.relname IN ('batch_run', 'job_run', 'job', 'error_log')
 ORDER BY c.relname, k.conname;

-- Triggers on the journal tables.
SELECT 'trigger | ' || c.relname || ' | ' || t.tgname || ' | ' || p.proname
  FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_proc p ON p.oid = t.tgfoid
 WHERE n.nspname = 'etl' AND c.relname IN ('batch_run', 'job_run', 'job', 'error_log') AND NOT t.tgisinternal
 ORDER BY 1;

-- History views read by the UI (definition, one line each).
SELECT 'view | ' || c.relname || ' | ' || replace(replace(pg_get_viewdef(c.oid, true), E'\n', ' '), '  ', ' ')
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'etl' AND c.relname IN ('v_integration_run_history', 'v_integration_error') AND c.relkind = 'v'
 ORDER BY 1;

-- Privileges of dwh_etl and dwh_app on the journal tables, sequences and the new target.
SELECT 'privilege | ' || n.nspname || '.' || c.relname || ' | ' || r.rolname
    || ' | select=' || has_table_privilege(r.oid, c.oid, 'SELECT')::text
    || ' insert=' || has_table_privilege(r.oid, c.oid, 'INSERT')::text
    || ' update=' || has_table_privilege(r.oid, c.oid, 'UPDATE')::text
    || ' delete=' || has_table_privilege(r.oid, c.oid, 'DELETE')::text
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  CROSS JOIN pg_roles r
 WHERE r.rolname IN ('dwh_etl', 'dwh_app')
   AND ((n.nspname = 'etl' AND c.relname IN ('batch_run', 'job_run', 'job', 'error_log', 'v_integration_run_history', 'v_integration_error'))
     OR (n.nspname = 'stg' AND c.relname = 'operation_collector_asan_pension_requests')
     OR (n.nspname = 'meta' AND c.relname IN ('source_object', 'source_system', 'source_object_snapshot', 'source_column_snapshot', 'metadata_capture')))
 ORDER BY 1;

SELECT 'sequence | ' || n.nspname || '.' || c.relname || ' | dwh_etl usage=' || has_sequence_privilege('dwh_etl', c.oid, 'USAGE')::text
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'etl' AND c.relkind = 'S'
 ORDER BY 1;

SELECT 'schema | ' || n.nspname || ' | owner=' || pg_get_userbyid(n.nspowner)
    || ' | dwh_etl usage=' || has_schema_privilege('dwh_etl', n.oid, 'USAGE')::text
    || ' create=' || has_schema_privilege('dwh_etl', n.oid, 'CREATE')::text
  FROM pg_namespace n WHERE n.nspname IN ('etl', 'meta', 'stg', 'ods', 'app', 'secret_store') ORDER BY 1;

-- Default privileges in schema etl (new tables/functions of package 010).
SELECT 'default_acl | ' || n.nspname || ' | ' || pg_get_userbyid(d.defaclrole) || ' | ' || d.defaclobjtype::text
    || ' | ' || array_to_string(d.defaclacl, ',')
  FROM pg_default_acl d JOIN pg_namespace n ON n.oid = d.defaclnamespace
 WHERE n.nspname = 'etl' ORDER BY 1;

-- Registered ETL jobs of OPERATION_COLLECTOR (configuration only), if dwh_app may read etl.job;
-- row counts of the run tables from the planner statistics (no table access needed).
SELECT 'job | ' || coalesce(CASE WHEN has_table_privilege(current_user,
         (SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'etl' AND c.relname = 'job'), 'SELECT')
       AND has_table_privilege(current_user,
         (SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'meta' AND c.relname = 'source_object'), 'SELECT')
       AND has_table_privilege(current_user,
         (SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'meta' AND c.relname = 'source_system'), 'SELECT')
       THEN replace(query_to_xml($q$SELECT j.* FROM etl.job j JOIN meta.source_object so ON so.source_object_id = j.source_object_id
              JOIN meta.source_system ss ON ss.source_system_id = so.source_system_id WHERE ss.code = 'OPERATION_COLLECTOR'$q$,
              true, false, '')::text, E'\n', ' ') END, 'not readable by ' || current_user);

SELECT 'estimated_rows | ' || c.relname || ' | ' || c.reltuples::bigint
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'etl' AND c.relname IN ('batch_run', 'job_run', 'job', 'error_log') ORDER BY 1;
