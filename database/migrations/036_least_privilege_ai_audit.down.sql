-- Rollback of package 036: gives back exactly the privileges kept in audit.revoked_grant_036, drops the audit
-- triggers of the assistant settings. Events already written to audit.security_event stay (the journal is append-only).
-- Run as dwh_admin:  psql -1 -v ON_ERROR_STOP=1 -f 036_least_privilege_ai_audit.down.sql
DO $pre$
BEGIN
  IF to_regclass('audit.revoked_grant_036') IS NULL THEN RAISE EXCEPTION 'STOP: 036 is not applied'; END IF;
END
$pre$;

DO $back$
DECLARE g record; n int := 0;
BEGIN
  FOR g IN SELECT * FROM audit.revoked_grant_036 ORDER BY 1, 2, 3, 4 LOOP
    IF g.object_kind = 'TABLE' THEN
      EXECUTE format('GRANT %s ON %s TO %s', g.privilege_type, g.object_name,
                     CASE WHEN g.grantee = 'PUBLIC' THEN 'PUBLIC' ELSE quote_ident(g.grantee) END);
    ELSE
      EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %s', g.object_name,
                     CASE WHEN g.grantee = 'PUBLIC' THEN 'PUBLIC' ELSE quote_ident(g.grantee) END);
    END IF;
    n := n + 1;
  END LOOP;
  RAISE NOTICE 'PRIVILEGES_RESTORED %', n;
END
$back$;

DROP TRIGGER IF EXISTS trg_audit_ai_report_object ON meta.ai_report_object;
DROP TRIGGER IF EXISTS trg_audit_ai_value_column ON meta.ai_value_column;
DROP TRIGGER IF EXISTS trg_audit_ai_glossary ON meta.ai_glossary;
DROP TRIGGER IF EXISTS trg_audit_ai_example ON meta.ai_example;
DROP FUNCTION audit.audit_ai_settings();
DROP TABLE audit.revoked_grant_036;

SELECT 'ROLLBACK_036_DONE' AS result;
