-- I08 step 2: how CRM run requests are stored and checked (queue table, its functions and
-- privileges), which permission codes exist, which objects the old Integration Control lists.
-- Read-only, catalog metadata and configuration rows only (no business data). Tables of app are
-- read through query_to_xml inside CASE, so a missing table or missing grant gives a note, not an error.
SELECT 'role | ' || current_user || ' | db=' || current_database() || ' | ro=' || current_setting('transaction_read_only');

-- Queue of CRM run requests: columns, constraints, triggers.
SELECT 'column | ' || n.nspname || '.' || c.relname || ' | ' || a.attnum || ' | ' || a.attname || ' | ' || format_type(a.atttypid, a.atttypmod)
    || ' | not_null=' || a.attnotnull::text || coalesce(' | default=' || pg_get_expr(d.adbin, d.adrelid), '')
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
  LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
 WHERE (n.nspname = 'etl' AND c.relname LIKE '%run_request%') OR (n.nspname = 'meta' AND c.relname = 'v_integration_control_object')
 ORDER BY n.nspname, c.relname, a.attnum;

SELECT 'constraint | ' || c.relname || ' | ' || k.conname || ' | ' || pg_get_constraintdef(k.oid)
  FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'etl' AND c.relname LIKE '%run_request%'
 ORDER BY 1;

SELECT 'trigger | ' || c.relname || ' | ' || t.tgname || ' | ' || p.proname
  FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_proc p ON p.oid = t.tgfoid
 WHERE n.nspname = 'etl' AND c.relname LIKE '%run_request%' AND NOT t.tgisinternal
 ORDER BY 1;

-- Functions of the queue and of permission checks: security, owner, who may execute, full text in one line.
SELECT 'function | ' || p.oid::regprocedure::text || ' | definer=' || p.prosecdef::text || ' | owner=' || pg_get_userbyid(p.proowner)
    || ' | dwh_app=' || has_function_privilege('dwh_app', p.oid, 'EXECUTE')::text
    || ' | dwh_etl=' || has_function_privilege('dwh_etl', p.oid, 'EXECUTE')::text
    || ' | ' || replace(replace(replace(pg_get_functiondef(p.oid), E'\r', ' '), E'\n', ' '), '  ', ' ')
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE (n.nspname IN ('app', 'etl') AND p.proname LIKE '%run_request%')
    OR (n.nspname = 'app' AND p.proname IN ('has_scoped_permission', 'has_permission', 'integration_admin_session_user'))
 ORDER BY 1;

-- Views over the queue: definition in one line.
SELECT 'view | ' || n.nspname || '.' || c.relname || ' | ' || replace(replace(pg_get_viewdef(c.oid, true), E'\n', ' '), '  ', ' ')
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind = 'v' AND ((n.nspname = 'etl' AND c.relname LIKE '%run_request%')
                            OR (n.nspname = 'meta' AND c.relname = 'v_integration_control_object'))
 ORDER BY 1;

-- Privileges of dwh_app / dwh_etl on the queue tables and views.
SELECT 'privilege | ' || n.nspname || '.' || c.relname || ' | ' || r.rolname
    || ' | select=' || has_table_privilege(r.oid, c.oid, 'SELECT')::text
    || ' insert=' || has_table_privilege(r.oid, c.oid, 'INSERT')::text
    || ' update=' || has_table_privilege(r.oid, c.oid, 'UPDATE')::text
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace CROSS JOIN pg_roles r
 WHERE r.rolname IN ('dwh_etl', 'dwh_app') AND n.nspname = 'etl' AND c.relname LIKE '%run_request%'
 ORDER BY 1;

-- Permission codes (configuration rows) and the objects of the old Integration Control.
SELECT 'permissions | ' || CASE WHEN to_regclass('app.permission') IS NULL THEN 'table app.permission not found'
       WHEN NOT has_table_privilege('app.permission', 'SELECT') THEN 'no SELECT on app.permission for ' || current_user
       ELSE query_to_xml('SELECT permission_code, is_active FROM app.permission ORDER BY permission_code', false, false, '')::text END;

SELECT 'integration_control_objects | ' || CASE WHEN to_regclass('meta.v_integration_control_object') IS NULL THEN 'view not found'
       WHEN NOT has_table_privilege('meta.v_integration_control_object', 'SELECT') THEN 'no SELECT for ' || current_user
       ELSE query_to_xml('SELECT source_object_id FROM meta.v_integration_control_object ORDER BY 1', false, false, '')::text END;

SELECT 'source_objects | ' || CASE WHEN NOT has_table_privilege('meta.source_object', 'SELECT')
            OR NOT has_table_privilege('meta.source_system', 'SELECT') THEN 'no SELECT on meta.source_object / source_system for ' || current_user
       ELSE query_to_xml('SELECT so.source_object_id, ss.code, so.source_schema, so.source_object, so.is_active FROM meta.source_object so JOIN meta.source_system ss ON ss.source_system_id = so.source_system_id ORDER BY 1', false, false, '')::text END;
