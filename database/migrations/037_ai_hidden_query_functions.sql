-- I14-07 (package 037), finding H1 of the internal security audit: functions that run a query given as text
-- read tables that EXPLAIN does not show, so the plan check of the assistant cannot see them
-- (schema_to_xml('dm', ...) returns every mart). DWH does not use them. EXECUTE is taken from PUBLIC and from
-- the working roles dwh_app, dwh_etl, dwh_ai_ro, dwh_dm_sql_ro (those that exist) on every overload of:
--   query/table/cursor/schema/database _to_xml, _to_xmlschema, _to_xml_and_xmlschema; ts_stat; ts_rewrite.
-- Every privilege taken is kept in audit.revoked_grant_037 (owner only); the rollback restores it exactly.
-- Report: docs/DWH_I14_07_SECURITY_AUDIT_REPORT_20261126.md. Code side: backend/src/dwh_api/ai_sql_guard.py.
-- Run as a superuser role (dwh_admin): psql -1 -v ON_ERROR_STOP=1 -f 037_ai_hidden_query_functions.sql
DO $pre$
BEGIN
  IF to_regprocedure('audit.audit_ai_settings()') IS NULL THEN
    RAISE EXCEPTION 'STOP: package 036 is not applied';
  END IF;
  IF to_regclass('audit.revoked_grant_037') IS NOT NULL THEN
    RAISE EXCEPTION 'STOP: 037 is already applied';
  END IF;
  IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
    RAISE EXCEPTION 'STOP: functions of pg_catalog need a superuser (dwh_admin)';
  END IF;
END
$pre$;

CREATE TABLE audit.revoked_grant_037 (
  function_signature text NOT NULL,   -- regprocedure text
  grantee text NOT NULL,              -- role name or PUBLIC
  PRIMARY KEY (function_signature, grantee)
);
REVOKE ALL ON audit.revoked_grant_037 FROM PUBLIC;

DO $revoke$
DECLARE
  f record; a record; n_functions int := 0; n_grants int := 0;
  v_roles text[] := ARRAY(SELECT rolname::text FROM pg_roles
                           WHERE rolname IN ('dwh_app', 'dwh_etl', 'dwh_ai_ro', 'dwh_dm_sql_ro') ORDER BY 1);
BEGIN
  FOR f IN SELECT p.oid::regprocedure::text AS sig, p.proowner, p.proacl
             FROM pg_proc p
            WHERE p.pronamespace = 'pg_catalog'::regnamespace
              AND (p.proname ~ '^(query|table|cursor|schema|database)_to_xml(schema|_and_xmlschema)?$'
                   OR p.proname IN ('ts_stat', 'ts_rewrite'))
            ORDER BY 1 LOOP
    n_functions := n_functions + 1;
    FOR a IN SELECT CASE WHEN x.grantee = 0 THEN 'PUBLIC' ELSE x.grantee::regrole::text END AS grantee
               FROM aclexplode(coalesce(f.proacl, acldefault('f', f.proowner))) x
              WHERE x.privilege_type = 'EXECUTE'
                AND (x.grantee = 0 OR x.grantee::regrole::text = ANY (v_roles)) LOOP
      INSERT INTO audit.revoked_grant_037 VALUES (f.sig, a.grantee);
      n_grants := n_grants + 1;
    END LOOP;
    EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', f.sig);
    IF cardinality(v_roles) > 0 THEN
      EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM %s', f.sig,
                     (SELECT string_agg(quote_ident(r), ', ') FROM unnest(v_roles) r));
    END IF;
  END LOOP;
  IF n_functions < 16 THEN
    RAISE EXCEPTION 'STOP: expected at least 16 functions, found %', n_functions;
  END IF;
  RAISE NOTICE 'FUNCTIONS % GRANTS_REVOKED %', n_functions, n_grants;
END
$revoke$;

SELECT 'MIGRATION_037_DONE' AS result;
