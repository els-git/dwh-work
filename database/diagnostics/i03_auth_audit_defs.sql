-- I03 step 2 (part 2): session, permission and audit functions used by the
-- existing app.integration_* functions. Read-only: definitions and catalog
-- metadata only (no table data, no secrets).
SELECT current_user || ' | ' || current_database() || ' | ' || current_setting('server_version');

SELECT pg_get_functiondef(p.oid)
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE (n.nspname = 'app' AND p.proname IN ('integration_admin_session_user', 'has_scoped_permission', 'has_permission'))
    OR (n.nspname = 'audit' AND p.proname = 'log_security_event')
 ORDER BY n.nspname, p.proname;

-- Constraints and columns of audit.security_event (event codes, sizes).
SELECT 'constraint | ' || conname || ' | ' || pg_get_constraintdef(oid)
  FROM pg_constraint WHERE conrelid = to_regclass('audit.security_event') ORDER BY conname;
SELECT 'column | ' || attname || ' | ' || format_type(atttypid, atttypmod) || ' | not_null=' || attnotnull::text
  FROM pg_attribute WHERE attrelid = to_regclass('audit.security_event') AND attnum > 0 AND NOT attisdropped ORDER BY attnum;

-- Columns of meta.source_system (for profile foreign key and scope).
SELECT 'source_system | ' || attname || ' | ' || format_type(atttypid, atttypmod) || ' | not_null=' || attnotnull::text
  FROM pg_attribute WHERE attrelid = to_regclass('meta.source_system') AND attnum > 0 AND NOT attisdropped ORDER BY attnum;
