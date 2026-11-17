-- I18 rollback of 020: removed rows and tables of the old CRM contour cannot be recreated by a script.
-- The data are brought back only from the copy pre-020 that E2 takes (dwh_backup.py, /backup/dwh).
-- This script removes only the marker meta.legacy_crm_removed(); in E1 it lets the package be applied again on the copy
-- (the second run finds nothing left to remove). Run as the owner:
--   psql -v ON_ERROR_STOP=1 -f 020_remove_legacy_crm.down.sql
DROP FUNCTION IF EXISTS meta.legacy_crm_removed();
SELECT 'ROLLBACK_020_DONE' AS result;
