-- I02-04 M-SS03 rollback: return secret_store to the 002 state.
-- Refuses and changes nothing once 003 has been used: any non-CREATE event,
-- any recorded actor or any binding key_id (dropping them would lose journal
-- facts). Restores the 001 body of validate_event_insert() verbatim and the
-- 001 constraint event_for_create_operation. Drops without CASCADE.
-- Run as the secret_store owner: psql -v ON_ERROR_STOP=1 -f 003_secret_store_journal_operations.down.sql
BEGIN;
LOCK TABLE secret_store.binding, secret_store.operation_event IN ACCESS EXCLUSIVE MODE;
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM secret_store.operation_event
              WHERE operation_type <> 'CREATE' OR actor_kind IS NOT NULL OR actor_ref IS NOT NULL)
     OR EXISTS (SELECT 1 FROM secret_store.binding WHERE key_id IS NOT NULL) THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: 003 journal facts exist (operation types, actors or key_id)';
  END IF;
END
$do$;
DROP TRIGGER binding_lifecycle_guard ON secret_store.binding;
DROP FUNCTION secret_store.validate_binding_lifecycle();
CREATE OR REPLACE FUNCTION secret_store.validate_event_insert() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, secret_store AS $fn$
DECLARE v_source bigint;
BEGIN
  IF NEW.operation_id IS DISTINCT FROM NEW.create_operation_id THEN
    RAISE EXCEPTION 'Operation correlation mismatch';
  END IF;
  IF NEW.phase = 'ATTEMPT' THEN
    IF NEW.code = 'PERMITTED_ATTEMPT' AND EXISTS (
      SELECT 1 FROM secret_store.binding b
       WHERE b.create_operation_id=NEW.create_operation_id
    ) THEN
      RAISE EXCEPTION 'ATTEMPT must precede PENDING';
    END IF;
    RETURN NEW;
  END IF;
  SELECT b.source_system_id INTO v_source
    FROM secret_store.binding b
   WHERE b.create_operation_id=NEW.create_operation_id FOR KEY SHARE;
  IF NOT FOUND OR v_source IS DISTINCT FROM NEW.source_system_id THEN
    RAISE EXCEPTION 'Operation binding/source mismatch';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM secret_store.operation_event e
     WHERE e.operation_id=NEW.operation_id AND e.phase='ATTEMPT'
       AND e.code='PERMITTED_ATTEMPT'
       AND e.source_system_id=NEW.source_system_id
  ) THEN
    RAISE EXCEPTION 'Permitted attempt required before outcome';
  END IF;
  RETURN NEW;
END;
$fn$;
REVOKE ALL ON FUNCTION secret_store.validate_event_insert() FROM PUBLIC;
ALTER TABLE secret_store.binding DROP CONSTRAINT binding_active_requires_key;
ALTER TABLE secret_store.binding DROP CONSTRAINT binding_key_id_format;
ALTER TABLE secret_store.binding DROP COLUMN key_id;
DROP INDEX secret_store.operation_event_target_idx;
ALTER TABLE secret_store.operation_event DROP CONSTRAINT actor_required;
ALTER TABLE secret_store.operation_event DROP CONSTRAINT actor_ref_format;
ALTER TABLE secret_store.operation_event DROP CONSTRAINT actor_kind_valid;
ALTER TABLE secret_store.operation_event DROP CONSTRAINT replaced_only_for_replace;
ALTER TABLE secret_store.operation_event DROP CONSTRAINT operation_correlation_by_type;
ALTER TABLE secret_store.operation_event DROP CONSTRAINT operation_type_valid;
ALTER TABLE secret_store.operation_event DROP COLUMN actor_ref;
ALTER TABLE secret_store.operation_event DROP COLUMN actor_kind;
ALTER TABLE secret_store.operation_event DROP COLUMN replaced_create_operation_id;
ALTER TABLE secret_store.operation_event DROP COLUMN operation_type;
ALTER TABLE secret_store.operation_event
  ADD CONSTRAINT event_for_create_operation CHECK (operation_id=create_operation_id);
COMMIT;
