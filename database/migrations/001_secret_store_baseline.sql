-- I02-03: NEW OBJECTS ONLY, requires approved scoped deployment in test dwh.
-- No access is granted to application or ETL roles.
CREATE SCHEMA secret_store;
REVOKE ALL ON SCHEMA secret_store FROM PUBLIC;

CREATE TABLE secret_store.binding (
  secret_ref uuid PRIMARY KEY,
  source_system_id bigint NOT NULL REFERENCES meta.source_system(source_system_id) ON DELETE RESTRICT,
  create_operation_id uuid NOT NULL UNIQUE,
  state text NOT NULL DEFAULT 'PENDING' CHECK (state IN ('PENDING','ACTIVE','REVOKED')),
  ciphertext_sha256 text,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  activated_at timestamptz,
  revoked_at timestamptz,
  CONSTRAINT ciphertext_digest_format CHECK (ciphertext_sha256 IS NULL OR ciphertext_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT binding_state_timestamps CHECK (
    (state='PENDING' AND activated_at IS NULL AND revoked_at IS NULL)
    OR (state='ACTIVE' AND activated_at IS NOT NULL AND revoked_at IS NULL AND ciphertext_sha256 IS NOT NULL)
    OR (state='REVOKED' AND revoked_at IS NOT NULL))
);
CREATE INDEX binding_source_state_idx ON secret_store.binding(source_system_id,state);

CREATE TABLE secret_store.operation_event (
  event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  operation_id uuid NOT NULL,
  create_operation_id uuid NOT NULL,
  source_system_id bigint NOT NULL REFERENCES meta.source_system(source_system_id) ON DELETE RESTRICT,
  phase text NOT NULL CHECK (phase IN ('ATTEMPT','OUTCOME')),
  code text NOT NULL,
  occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CONSTRAINT operation_code_by_phase CHECK (
    (phase='ATTEMPT' AND code IN ('PERMITTED_ATTEMPT','DENIED_ATTEMPT'))
    OR (phase='OUTCOME' AND code IN ('SUCCEEDED','FAILED'))),
  CONSTRAINT operation_once_per_phase UNIQUE (operation_id,phase),
  CONSTRAINT event_for_create_operation CHECK (operation_id=create_operation_id)
);
CREATE INDEX operation_event_source_created_idx ON secret_store.operation_event(source_system_id,occurred_at DESC);

CREATE FUNCTION secret_store.reject_binding_mutation() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, secret_store AS $fn$
BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Binding deletion prohibited'; END IF;
  IF NEW.secret_ref IS DISTINCT FROM OLD.secret_ref OR
     NEW.source_system_id IS DISTINCT FROM OLD.source_system_id OR
     NEW.create_operation_id IS DISTINCT FROM OLD.create_operation_id OR
     NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'Binding owner and identity immutable';
  END IF;
  IF OLD.state='REVOKED' OR
     NOT ((OLD.state='PENDING' AND NEW.state IN ('ACTIVE','REVOKED'))
           OR (OLD.state='ACTIVE' AND NEW.state='REVOKED')) THEN
    RAISE EXCEPTION 'Binding transition prohibited';
  END IF;
  IF OLD.state='PENDING' AND NEW.state='ACTIVE' THEN
    IF NEW.activated_at IS NULL OR NEW.ciphertext_sha256 IS NULL OR NOT EXISTS (
      SELECT 1 FROM secret_store.operation_event e
       WHERE e.operation_id=OLD.create_operation_id
         AND e.source_system_id=OLD.source_system_id
         AND e.phase='OUTCOME' AND e.code='SUCCEEDED'
    ) THEN
      RAISE EXCEPTION 'Unverified activation facts';
    END IF;
  END IF;
  IF OLD.state='ACTIVE' AND NEW.state='REVOKED' AND
     (NEW.activated_at IS DISTINCT FROM OLD.activated_at OR
      NEW.ciphertext_sha256 IS DISTINCT FROM OLD.ciphertext_sha256) THEN
    RAISE EXCEPTION 'Activation facts immutable';
  END IF;
  RETURN NEW;
END;
$fn$;
CREATE TRIGGER binding_guard BEFORE UPDATE OR DELETE ON secret_store.binding
FOR EACH ROW EXECUTE FUNCTION secret_store.reject_binding_mutation();

CREATE FUNCTION secret_store.reject_event_mutation() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, secret_store AS $fn$
BEGIN RAISE EXCEPTION 'Operation journal is append-only'; END;
$fn$;
CREATE TRIGGER event_append_only BEFORE UPDATE OR DELETE ON secret_store.operation_event
FOR EACH ROW EXECUTE FUNCTION secret_store.reject_event_mutation();

-- Event admission: ATTEMPT is independent of binding, because it must COMMIT first.
-- OUTCOME is only permitted after an existing binding and previously committed ATTEMPT.
-- These triggers do NOT verify durable COMMIT or ciphertext/file integrity.
CREATE FUNCTION secret_store.validate_event_insert() RETURNS trigger
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
CREATE TRIGGER event_insert_guard BEFORE INSERT ON secret_store.operation_event
 FOR EACH ROW EXECUTE FUNCTION secret_store.validate_event_insert();

-- PENDING itself requires a committed permitted attempt. This is a structural
-- check only; visibility proves a prior committed transaction in the intended
-- connection model, not crash-safe receipt verification by application code.
CREATE FUNCTION secret_store.validate_binding_insert() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, secret_store AS $fn$
BEGIN
  IF NEW.state IS DISTINCT FROM 'PENDING' OR NOT EXISTS (
    SELECT 1 FROM secret_store.operation_event e
     WHERE e.operation_id=NEW.create_operation_id
       AND e.create_operation_id=NEW.create_operation_id
       AND e.source_system_id=NEW.source_system_id
       AND e.phase='ATTEMPT' AND e.code='PERMITTED_ATTEMPT'
  ) THEN
    RAISE EXCEPTION 'Committed permitted attempt required before PENDING';
  END IF;
  RETURN NEW;
END;
$fn$;
CREATE TRIGGER binding_insert_guard BEFORE INSERT ON secret_store.binding
 FOR EACH ROW EXECUTE FUNCTION secret_store.validate_binding_insert();

-- Deliberately no writable SECURITY DEFINER API: authenticating session/ETL actor,
-- independent ATTEMPT commit, file verification and trusted receipts are unsolved.
-- Do not issue direct table privileges or function EXECUTE to dwh_app or PUBLIC.
REVOKE ALL ON ALL TABLES IN SCHEMA secret_store FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA secret_store FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA secret_store FROM PUBLIC;
-- PostgreSQL schema owner retains control; service role not yet designed.
