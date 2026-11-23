-- Rollback of 029 (I13-05): drops the journal view functions. The journals and the events AUDIT_LOG_VIEWED /
-- SECRET_JOURNAL_VIEWED already written stay (journals are never cleaned). Run as dwh_admin:
--   psql -1 -v ON_ERROR_STOP=1 -f 029_audit_log_view.down.sql
DROP FUNCTION app.audit_log_page(text, timestamptz, timestamptz, text, text, text, text, bigint, integer);
DROP FUNCTION app.audit_log_filters(text);
DROP FUNCTION app.secret_journal_page(text, bigint, timestamptz, timestamptz, text, bigint, integer);
DROP FUNCTION app.audit_mask_details(jsonb);
DROP FUNCTION app.audit_admin_session_user(text);
SELECT 'ROLLBACK_029_DONE' AS result;
