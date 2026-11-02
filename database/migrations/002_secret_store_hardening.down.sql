-- I02-04 M-SS02 rollback: return secret_store to the 001_secret_store_baseline state.
-- Refuses and changes nothing if any binding has a profile_code (dropping the
-- column would lose it). Drops without CASCADE: unexpected dependents abort it.
-- Run as the secret_store owner: psql -v ON_ERROR_STOP=1 -f 002_secret_store_hardening.down.sql
BEGIN;
LOCK TABLE secret_store.binding IN ACCESS EXCLUSIVE MODE;
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM secret_store.binding WHERE profile_code IS NOT NULL) THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: bindings with profile_code exist';
  END IF;
END
$do$;
DROP TRIGGER binding_profile_guard ON secret_store.binding;
DROP FUNCTION secret_store.reject_profile_change();
DROP INDEX secret_store.binding_one_active_per_profile;
ALTER TABLE secret_store.binding DROP CONSTRAINT binding_active_requires_profile;
ALTER TABLE secret_store.binding DROP CONSTRAINT binding_profile_code_format;
ALTER TABLE secret_store.binding DROP COLUMN profile_code;
DROP TRIGGER event_truncate_guard ON secret_store.operation_event;
DROP TRIGGER binding_truncate_guard ON secret_store.binding;
DROP FUNCTION secret_store.reject_truncate();
COMMIT;
