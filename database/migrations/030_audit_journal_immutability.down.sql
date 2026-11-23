-- Rollback of 030 (I13-14): removes the TRUNCATE guard and gives dwh_etl back UPDATE, DELETE, TRUNCATE on
-- audit.security_event (the state before 030). Journal rows are not touched. Run as dwh_admin:
--   psql -1 -v ON_ERROR_STOP=1 -f 030_audit_journal_immutability.down.sql
DROP TRIGGER trg_security_event_no_truncate ON audit.security_event;
DROP FUNCTION audit.reject_security_event_truncate();
GRANT UPDATE, DELETE, TRUNCATE ON audit.security_event TO dwh_etl;
SELECT 'ROLLBACK_030_DONE' AS result;
