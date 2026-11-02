-- I02-04 M-SS03: journal operation type, actor and master-key version
-- (review findings G3, G4, G5). Requires 001 and 002.
--
-- operation_event.operation_type: CREATE | REPLACE | REVOKE | READ.
--   CREATE, REPLACE: operation_id = create_operation_id (the new binding);
--     REPLACE also names the binding it replaces (replaced_create_operation_id).
--   REVOKE, READ: operation_id <> create_operation_id (the existing binding).
--   Rows written before 003 were CREATE (001 forced operation_id =
--   create_operation_id); they receive operation_type = 'CREATE'.
-- operation_event.actor_kind / actor_ref: verified actor (A05), required for
--   new rows; rows before 003 keep NULL (NOT VALID rule).
-- binding.key_id: non-secret master-key version (K08), required for new
--   ACTIVE bindings (NOT VALID rule keeps the two ACTIVE rows from 28-29.10).
-- Lifecycle: ACTIVE needs a SUCCEEDED CREATE/REPLACE outcome (001) and a
--   key_id; REPLACE activates only after the replaced binding is REVOKED in
--   the same source/profile; REVOKED needs a SUCCEEDED REVOKE or REPLACE
--   outcome, except a PENDING whose own CREATE/REPLACE outcome FAILED.
--   READ may be permitted only for an ACTIVE binding with a profile (S03).
-- Run as the secret_store owner:
--   psql -1 -v ON_ERROR_STOP=1 -f 003_secret_store_journal_operations.sql
-- No access is granted to application or ETL roles.

ALTER TABLE secret_store.operation_event
  ADD COLUMN operation_type text NOT NULL DEFAULT 'CREATE';
ALTER TABLE secret_store.operation_event ALTER COLUMN operation_type DROP DEFAULT;
ALTER TABLE secret_store.operation_event
  ADD COLUMN replaced_create_operation_id uuid,
  ADD COLUMN actor_kind text,
  ADD COLUMN actor_ref text;

ALTER TABLE secret_store.operation_event DROP CONSTRAINT event_for_create_operation;
ALTER TABLE secret_store.operation_event
  ADD CONSTRAINT operation_type_valid
    CHECK (operation_type IN ('CREATE', 'REPLACE', 'REVOKE', 'READ')),
  ADD CONSTRAINT operation_correlation_by_type CHECK (
    (operation_type IN ('CREATE', 'REPLACE')) = (operation_id = create_operation_id)),
  ADD CONSTRAINT replaced_only_for_replace CHECK (
    (operation_type = 'REPLACE') = (replaced_create_operation_id IS NOT NULL)),
  ADD CONSTRAINT actor_kind_valid CHECK (actor_kind IN ('DWH_USER', 'SERVICE')),
  ADD CONSTRAINT actor_ref_format CHECK (actor_ref ~ '^[A-Za-z0-9_.:-]{1,128}$'),
  ADD CONSTRAINT actor_required CHECK (actor_kind IS NOT NULL AND actor_ref IS NOT NULL) NOT VALID;
CREATE INDEX operation_event_target_idx
  ON secret_store.operation_event(create_operation_id, operation_type, phase);

ALTER TABLE secret_store.binding ADD COLUMN key_id text;
ALTER TABLE secret_store.binding
  ADD CONSTRAINT binding_key_id_format CHECK (key_id IS NULL OR key_id ~ '^[A-Za-z0-9_.-]{1,64}$'),
  ADD CONSTRAINT binding_active_requires_key CHECK (state <> 'ACTIVE' OR key_id IS NOT NULL) NOT VALID;

-- Event admission (replaces the 001 body; 003 down restores it).
CREATE OR REPLACE FUNCTION secret_store.validate_event_insert() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, secret_store AS $fn$
DECLARE
  v_source bigint;
  v_state text;
  v_profile text;
BEGIN
  IF NEW.phase = 'ATTEMPT' AND NEW.code = 'DENIED_ATTEMPT' THEN
    RETURN NEW;  -- denials are always recorded (A03)
  END IF;

  IF NEW.phase = 'ATTEMPT' THEN
    IF NEW.operation_type IN ('CREATE', 'REPLACE') THEN
      IF EXISTS (SELECT 1 FROM secret_store.binding b
                  WHERE b.create_operation_id = NEW.create_operation_id) THEN
        RAISE EXCEPTION 'ATTEMPT must precede PENDING';
      END IF;
      IF NEW.operation_type = 'REPLACE' THEN
        SELECT b.source_system_id, b.state INTO v_source, v_state
          FROM secret_store.binding b
         WHERE b.create_operation_id = NEW.replaced_create_operation_id FOR KEY SHARE;
        IF NOT FOUND OR v_source IS DISTINCT FROM NEW.source_system_id OR v_state <> 'ACTIVE' THEN
          RAISE EXCEPTION 'REPLACE requires an ACTIVE binding of the same source';
        END IF;
      END IF;
      RETURN NEW;
    END IF;
    -- REVOKE, READ: the target binding must exist in the same source.
    SELECT b.source_system_id, b.state, b.profile_code INTO v_source, v_state, v_profile
      FROM secret_store.binding b
     WHERE b.create_operation_id = NEW.create_operation_id FOR KEY SHARE;
    IF NOT FOUND OR v_source IS DISTINCT FROM NEW.source_system_id THEN
      RAISE EXCEPTION 'Operation binding/source mismatch';
    END IF;
    IF NEW.operation_type = 'REVOKE' AND v_state = 'REVOKED' THEN
      RAISE EXCEPTION 'Binding already revoked';
    END IF;
    IF NEW.operation_type = 'READ' AND (v_state <> 'ACTIVE' OR v_profile IS NULL) THEN
      RAISE EXCEPTION 'READ requires an ACTIVE binding with a profile';
    END IF;
    RETURN NEW;
  END IF;

  -- OUTCOME: binding of the same source and a matching permitted attempt.
  SELECT b.source_system_id INTO v_source
    FROM secret_store.binding b
   WHERE b.create_operation_id = NEW.create_operation_id FOR KEY SHARE;
  IF NOT FOUND OR v_source IS DISTINCT FROM NEW.source_system_id THEN
    RAISE EXCEPTION 'Operation binding/source mismatch';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM secret_store.operation_event e
     WHERE e.operation_id = NEW.operation_id AND e.phase = 'ATTEMPT'
       AND e.code = 'PERMITTED_ATTEMPT'
       AND e.operation_type = NEW.operation_type
       AND e.create_operation_id = NEW.create_operation_id
       AND e.source_system_id = NEW.source_system_id
       AND e.replaced_create_operation_id IS NOT DISTINCT FROM NEW.replaced_create_operation_id
  ) THEN
    RAISE EXCEPTION 'Permitted attempt required before outcome';
  END IF;
  RETURN NEW;
END;
$fn$;

-- Binding lifecycle rules added by 003 (the 001 and 002 guards stay in place).
CREATE FUNCTION secret_store.validate_binding_lifecycle() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, secret_store AS $fn$
DECLARE
  v_replaced uuid;
  r record;
BEGIN
  IF NEW.key_id IS DISTINCT FROM OLD.key_id AND NOT (OLD.state = 'PENDING' AND NEW.state = 'ACTIVE' AND OLD.key_id IS NULL) THEN
    RAISE EXCEPTION 'Binding key_id immutable';
  END IF;

  IF OLD.state = 'PENDING' AND NEW.state = 'ACTIVE' THEN
    SELECT e.replaced_create_operation_id INTO v_replaced
      FROM secret_store.operation_event e
     WHERE e.operation_id = OLD.create_operation_id AND e.phase = 'ATTEMPT'
       AND e.code = 'PERMITTED_ATTEMPT' AND e.operation_type = 'REPLACE';
    IF FOUND THEN
      SELECT b.state, b.source_system_id, b.profile_code INTO r
        FROM secret_store.binding b WHERE b.create_operation_id = v_replaced;
      IF r.state IS DISTINCT FROM 'REVOKED' OR r.source_system_id IS DISTINCT FROM NEW.source_system_id
         OR r.profile_code IS DISTINCT FROM NEW.profile_code THEN
        RAISE EXCEPTION 'REPLACE: revoke the replaced binding of the same profile first';
      END IF;
    END IF;
  END IF;

  IF NEW.state = 'REVOKED' AND OLD.state <> 'REVOKED' THEN
    IF NOT EXISTS (
      -- An explicit REVOKE of this binding succeeded ...
      SELECT 1 FROM secret_store.operation_event e
       WHERE e.create_operation_id = OLD.create_operation_id AND e.operation_type = 'REVOKE'
         AND e.phase = 'OUTCOME' AND e.code = 'SUCCEEDED'
      UNION ALL
      -- ... or a REPLACE of this binding succeeded ...
      SELECT 1 FROM secret_store.operation_event e
       WHERE e.replaced_create_operation_id = OLD.create_operation_id AND e.operation_type = 'REPLACE'
         AND e.phase = 'OUTCOME' AND e.code = 'SUCCEEDED'
      UNION ALL
      -- ... or this is a PENDING whose own CREATE/REPLACE failed.
      SELECT 1 FROM secret_store.operation_event e
       WHERE OLD.state = 'PENDING' AND e.operation_id = OLD.create_operation_id
         AND e.phase = 'OUTCOME' AND e.code = 'FAILED'
    ) THEN
      RAISE EXCEPTION 'Revocation requires a succeeded REVOKE or REPLACE outcome';
    END IF;
  END IF;
  RETURN NEW;
END;
$fn$;
CREATE TRIGGER binding_lifecycle_guard BEFORE UPDATE ON secret_store.binding
FOR EACH ROW EXECUTE FUNCTION secret_store.validate_binding_lifecycle();

REVOKE ALL ON FUNCTION secret_store.validate_binding_lifecycle() FROM PUBLIC;
REVOKE ALL ON FUNCTION secret_store.validate_event_insert() FROM PUBLIC;
