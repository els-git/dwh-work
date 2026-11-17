-- I18 rollback of 021: removes the functions object_table_*. Tables created or changed from the UI stay as they
-- are (their data are kept; a version published from the UI stays published). Afterwards a new table or column
-- again needs a change proposal applied by a SQL package. Run as the owner:
--   psql -1 -v ON_ERROR_STOP=1 -f 021_object_table_ui_ddl.down.sql
DROP FUNCTION IF EXISTS app.object_table_publish(text, text, bigint, integer, boolean);
DROP FUNCTION IF EXISTS app.object_table_preview(text, text, bigint);
DROP FUNCTION IF EXISTS meta.object_table_retype_losses(text, text, text, text, text);
DROP FUNCTION IF EXISTS meta.object_table_plan(text, bigint);
DROP FUNCTION IF EXISTS meta.object_table_impact(text, text, text[]);
DROP FUNCTION IF EXISTS meta.object_table_other_users(text, text, text, bigint);
DROP FUNCTION IF EXISTS meta.object_table_fields(text, bigint);
DROP FUNCTION IF EXISTS meta.object_table_draft(text, bigint);
DROP FUNCTION IF EXISTS meta.object_table_pg_type(text);
SELECT 'ROLLBACK_021_DONE' AS result;
