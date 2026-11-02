-- I02-04 M-SS01 rollback: remove secret_store created by 001_secret_store_baseline.sql.
-- Refuses and changes nothing if any binding or journal row exists (A10: audit
-- history is retained). Drops without CASCADE: unexpected dependents abort it.
-- Run: psql -v ON_ERROR_STOP=1 -f 001_secret_store_baseline.down.sql
BEGIN;
LOCK TABLE secret_store.binding, secret_store.operation_event IN ACCESS EXCLUSIVE MODE;
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM secret_store.binding)
     OR EXISTS (SELECT 1 FROM secret_store.operation_event) THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: secret_store contains rows; audit history must be retained';
  END IF;
END
$do$;
DROP TABLE secret_store.binding;
DROP TABLE secret_store.operation_event;
DROP FUNCTION secret_store.reject_binding_mutation();
DROP FUNCTION secret_store.reject_event_mutation();
DROP FUNCTION secret_store.validate_event_insert();
DROP FUNCTION secret_store.validate_binding_insert();
DROP SCHEMA secret_store;
COMMIT;
