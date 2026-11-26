-- I13 step 2b: read-only. Triggers of app/meta/etl tables and whether their function writes the audit
-- journal (to see whether user, role, module and access changes are audited by triggers, not in the
-- functions themselves). Catalog only. Run as dwh_app:
--   sudo /opt/dwh/current/venv/bin/python /opt/dwh/current/src/tools/server/dwh_readonly_sql.py /srv/dwh/incoming/i13_step2b_audit_triggers.sql
SELECT n.nspname AS schema, c.relname AS table_name, t.tgname AS trigger_name,
       pn.nspname || '.' || p.proname AS trigger_function,
       (p.prosrc ~* 'security_event|log_security_event') AS writes_audit,
       t.tgenabled AS enabled
  FROM pg_trigger t
  JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_proc p ON p.oid = t.tgfoid JOIN pg_namespace pn ON pn.oid = p.pronamespace
 WHERE NOT t.tgisinternal AND n.nspname IN ('app', 'meta', 'etl')
 ORDER BY 1, 2, 3
