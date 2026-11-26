-- I14-07, step 3: inventory of the audit schema before a decision on finding N4 (read-only, as dwh_app).
--   sudo /opt/dwh/current/venv/bin/python /opt/dwh/current/src/tools/server/dwh_readonly_sql.py \
--        /opt/dwh/current/src/database/diagnostics/i14_07_audit_schema_inventory.sql
-- 1. Privileges of the working roles on every table of schema audit
SELECT 'Q1' AS q, c.oid::regclass::text AS audit_table, r.rolname AS role,
       string_agg(p.priv, ',' ORDER BY p.priv) AS privileges
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'audit'
  CROSS JOIN pg_roles r
  CROSS JOIN unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) p(priv)
 WHERE c.relkind IN ('r', 'p', 'v') AND r.rolname IN ('dwh_app', 'dwh_etl', 'dwh_ai_ro', 'dwh_dm_sql_ro', 'dwh_report')
   AND has_table_privilege(r.rolname, c.oid, p.priv)
 GROUP BY 1, 2, 3 ORDER BY 2, 3;
-- 2. Functions running with the caller's rights (not SECURITY DEFINER) that touch schema audit,
--    and whether dwh_etl / dwh_app may call them: these need the direct table rights
SELECT 'Q2' AS q, p.oid::regprocedure::text AS function_name,
       has_function_privilege('dwh_etl', p.oid, 'EXECUTE') AS etl_may_call,
       has_function_privilege('dwh_app', p.oid, 'EXECUTE') AS app_may_call,
       (p.prosrc ~* 'audit\.security_event') AS security_event,
       (p.prosrc ~* 'audit\.revoked_grant') AS revoked_grant
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE NOT p.prosecdef AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
   AND p.prosrc ~* 'audit\.(security_event|revoked_grant)'
 ORDER BY 2;
-- 3. Default privileges of schema audit (what new tables get)
SELECT 'Q3' AS q, pg_get_userbyid(d.defaclrole) AS owner, d.defaclobjtype AS object_type,
       pg_get_userbyid(a.grantee) AS grantee, string_agg(a.privilege_type, ',' ORDER BY a.privilege_type) AS privileges
  FROM pg_default_acl d JOIN pg_namespace n ON n.oid = d.defaclnamespace AND n.nspname = 'audit',
       aclexplode(d.defaclacl) a
 WHERE a.grantee <> d.defaclrole
 GROUP BY 1, 2, 3, 4 ORDER BY 2, 3, 4;
-- 4. Rows of the audit tables of packages 036 and 037 (counts only; the rollback scripts GRANT from these rows)
SELECT 'Q4' AS q, c.oid::regclass::text AS audit_table, c.reltuples::bigint AS estimated_rows,
       pg_get_userbyid(c.relowner) AS owner
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'audit'
 WHERE c.relkind = 'r' AND c.relname LIKE 'revoked_grant_%' ORDER BY 2
