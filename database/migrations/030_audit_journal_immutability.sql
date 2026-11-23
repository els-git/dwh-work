-- I13-14 (package 030): the audit journal cannot be changed or cleared by the working roles.
-- Plan: docs/DWH_I13_14_AUDIT_IMMUTABILITY_PLAN_APPROVED_20261123.md (A1-A3 = A). Inventory 23.11.2026: rows of
-- audit.security_event are already protected by trg_security_event_append_only (UPDATE/DELETE), but TRUNCATE is not
-- guarded and dwh_etl holds UPDATE, DELETE and TRUNCATE on the table.
--   A1  REVOKE UPDATE, DELETE, TRUNCATE ON audit.security_event FROM dwh_etl (INSERT and SELECT stay)
--   A2  trigger trg_security_event_no_truncate: TRUNCATE is refused for everybody who does not drop the trigger
--   A3  dwh_admin stays as it is
-- Nothing else changes. Run as the owner of schema audit (dwh_admin):
--   psql -1 -v ON_ERROR_STOP=1 -f 030_audit_journal_immutability.sql
DO $pre$
BEGIN
  IF to_regclass('audit.security_event') IS NULL THEN RAISE EXCEPTION 'STOP: audit.security_event not found'; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'audit.security_event'::regclass
                  AND tgname = 'trg_security_event_append_only' AND NOT tgisinternal) THEN
    RAISE EXCEPTION 'STOP: trg_security_event_append_only not found on audit.security_event';
  END IF;
  IF to_regprocedure('audit.reject_security_event_truncate()') IS NOT NULL THEN
    RAISE EXCEPTION 'STOP: 030 is already applied';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dwh_etl') THEN RAISE EXCEPTION 'STOP: role dwh_etl not found'; END IF;
END
$pre$;

CREATE FUNCTION audit.reject_security_event_truncate() RETURNS trigger
LANGUAGE plpgsql SET search_path TO 'pg_catalog' AS $fn$
BEGIN
  RAISE EXCEPTION 'TRUNCATE of audit.security_event prohibited';
END
$fn$;
REVOKE ALL ON FUNCTION audit.reject_security_event_truncate() FROM PUBLIC;

CREATE TRIGGER trg_security_event_no_truncate BEFORE TRUNCATE ON audit.security_event
  FOR EACH STATEMENT EXECUTE FUNCTION audit.reject_security_event_truncate();

REVOKE UPDATE, DELETE, TRUNCATE ON audit.security_event FROM dwh_etl;

SELECT 'MIGRATION_030_DONE' AS result;
