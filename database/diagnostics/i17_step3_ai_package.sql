-- I17 step 3: what package 016 (AI assistant) builds on. Read-only, catalog metadata and configuration
-- rows only (no business data). Tables of app are read through query_to_xml inside CASE, so a missing
-- table or missing grant gives a note, not an error.
SELECT 'role | ' || current_user || ' | db=' || current_database() || ' | ro=' || current_setting('transaction_read_only') || ' | ' || version();

-- Permission catalog: columns, constraints, rows (codes are configuration, not business data).
SELECT 'column | app.' || c.relname || ' | ' || a.attnum || ' | ' || a.attname || ' | ' || format_type(a.atttypid, a.atttypmod)
    || ' | not_null=' || a.attnotnull::text || coalesce(' | default=' || pg_get_expr(d.adbin, d.adrelid), '')
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
  LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
 WHERE n.nspname = 'app' AND c.relname IN ('permission', 'role', 'role_permission_history', 'user_role_history')
 ORDER BY c.relname, a.attnum;

SELECT 'constraint | ' || c.relname || ' | ' || k.conname || ' | ' || pg_get_constraintdef(k.oid)
  FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'app' AND c.relname IN ('permission', 'role_permission_history')
 ORDER BY 1;

SELECT 'permissions | ' || CASE WHEN to_regclass('app.permission') IS NULL THEN 'table app.permission not found'
       WHEN NOT has_table_privilege('app.permission', 'SELECT') THEN 'no SELECT on app.permission for ' || current_user
       ELSE replace(query_to_xml('SELECT * FROM app.permission ORDER BY 1', false, false, '')::text, E'\n', ' ') END;

-- How a session gets its permissions (SUPERADMIN / ADMIN / roles): function text in one line.
SELECT 'function | ' || p.oid::regprocedure::text || ' | definer=' || p.prosecdef::text || ' | owner=' || pg_get_userbyid(p.proowner)
    || ' | dwh_app=' || has_function_privilege('dwh_app', p.oid, 'EXECUTE')::text
    || ' | ' || replace(replace(replace(pg_get_functiondef(p.oid), E'\r', ' '), E'\n', ' '), '  ', ' ')
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname = 'app' AND p.proname IN ('auth_get_permissions', 'integration_run_admin_session_user', 'integration_admin_session_user')
 ORDER BY 1;

-- Reporting layer dm: owner, objects, who may read them, default privileges.
SELECT 'schema | ' || nspname || ' | owner=' || pg_get_userbyid(nspowner) || ' | acl=' || coalesce(nspacl::text, '-')
  FROM pg_namespace WHERE nspname IN ('dm', 'ods', 'stg', 'meta', 'app', 'public') ORDER BY 1;

SELECT 'dm_object | ' || c.relname || ' | kind=' || c.relkind::text || ' | owner=' || pg_get_userbyid(c.relowner)
    || ' | acl=' || coalesce(c.relacl::text, '-')
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'dm' AND c.relkind IN ('r', 'v', 'm', 'p') ORDER BY 1;

SELECT 'default_acl | ' || coalesce(n.nspname, '(all)') || ' | owner=' || pg_get_userbyid(d.defaclrole)
    || ' | type=' || d.defaclobjtype::text || ' | ' || d.defaclacl::text
  FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace ORDER BY 1;

-- Columns of the reporting views (names and types only), for the model description and value lists.
SELECT 'dm_column | ' || c.relname || ' | ' || a.attnum || ' | ' || a.attname || ' | ' || format_type(a.atttypid, a.atttypmod)
    || coalesce(' | comment=' || col_description(c.oid, a.attnum), '')
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
 WHERE n.nspname = 'dm' ORDER BY c.relname, a.attnum;

-- Roles of the cluster (names and attributes only) and installed extensions.
SELECT 'pg_role | ' || rolname || ' | login=' || rolcanlogin::text || ' | super=' || rolsuper::text
    || ' | config=' || coalesce(array_to_string(rolconfig, ','), '-')
  FROM pg_roles WHERE rolname NOT LIKE 'pg\_%' ORDER BY 1;

SELECT 'extension | ' || extname || ' | ' || extversion || ' | schema=' || extnamespace::regnamespace::text FROM pg_extension ORDER BY 1;

-- Existing app tables (to place the journal of AI requests next to similar ones).
SELECT 'app_table | ' || c.relname || ' | owner=' || pg_get_userbyid(c.relowner)
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'app' AND c.relkind = 'r' ORDER BY 1
