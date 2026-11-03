-- I03 rollback of 005: remove source connections and their API functions.
-- Refuses and changes nothing if any source connection exists (dropping the
-- table would lose it). secret_store rows written through the functions stay.
-- Run as the schema owner: psql -v ON_ERROR_STOP=1 -f 005_source_connection.down.sql
BEGIN;
LOCK TABLE meta.source_connection IN ACCESS EXCLUSIVE MODE;
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM meta.source_connection) THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: source connections exist';
  END IF;
END
$do$;
DROP FUNCTION app.integration_source_secret_revoke(text, bigint);
DROP FUNCTION app.integration_source_secret_complete(text, bigint, uuid, boolean, text, text);
DROP FUNCTION app.integration_source_secret_begin(text, bigint);
DROP FUNCTION app.integration_source_secret_connection(bigint);
DROP FUNCTION app.integration_source_secret_scope(bigint, bigint, text, uuid);
DROP FUNCTION app.integration_source_connection_set_status(text, bigint, integer, text);
DROP FUNCTION app.integration_source_connection_save(text, bigint, text, jsonb, integer);
DROP FUNCTION app.integration_source_connection_get(text, bigint);
DROP TABLE meta.source_connection;
DROP FUNCTION meta.source_connection_reject_truncate();
DROP FUNCTION meta.source_connection_guard();
DROP FUNCTION meta.integration_config_has_secret_key(jsonb);
COMMIT;
