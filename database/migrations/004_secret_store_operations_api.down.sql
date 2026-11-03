-- I02-04 M-SS04 rollback: return secret_store to the 003 state.
-- The functions hold no data; journal rows written through them stay.
-- Run as the secret_store owner: psql -v ON_ERROR_STOP=1 -f 004_secret_store_operations_api.down.sql
BEGIN;
DROP FUNCTION secret_store.activate_binding(uuid, text, text);
DROP FUNCTION secret_store.finish_operation(uuid, boolean);
DROP FUNCTION secret_store.register_pending(uuid, uuid, text);
DROP FUNCTION secret_store.record_denied_attempt(text, bigint, text, text, uuid, uuid);
DROP FUNCTION secret_store.begin_operation(text, bigint, text, text, uuid, uuid);
COMMIT;
