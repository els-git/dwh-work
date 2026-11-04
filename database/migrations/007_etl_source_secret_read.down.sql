-- I04 rollback of 007: remove the ETL credential READ functions.
-- secret_store READ events already written stay: they are history.
-- Run as the schema owner: psql -v ON_ERROR_STOP=1 -f 007_etl_source_secret_read.down.sql
BEGIN;
DROP FUNCTION etl.source_secret_read_finish(uuid, boolean);
DROP FUNCTION etl.source_secret_read_begin(text);
COMMIT;
