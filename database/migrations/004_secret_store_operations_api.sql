-- I02-04 M-SS04: write functions for secret_store (review finding G6, part 1).
-- One audited code path for every operation instead of direct INSERT/UPDATE:
--   begin_operation()       ATTEMPT PERMITTED_ATTEMPT; server-generated operation_id (A01)
--   record_denied_attempt() ATTEMPT DENIED_ATTEMPT (A03)
--   register_pending()      PENDING binding for a CREATE/REPLACE operation
--   finish_operation()      OUTCOME; on FAILED a CREATE/REPLACE PENDING is revoked,
--                           on SUCCEEDED a REVOKE revokes its binding; repeating
--                           the same outcome is a no-op (A09)
--   activate_binding()      PENDING -> ACTIVE after SUCCEEDED; for REPLACE revokes
--                           the replaced binding first, in the same transaction (S06)
-- ATTEMPT and OUTCOME are separate calls so that each can COMMIT on its own (A04).
-- Authorization stays with the trusted caller: these functions do not verify DWH
-- sessions. The 001-003 triggers still enforce every rule underneath.
-- SECURITY DEFINER with a fixed search_path; EXECUTE is granted to NOBODY here.
-- Roles and grants are a separate package together with I03, because roles are
-- cluster-wide and could not be isolated in the E1 copy.
-- Requires 001-003. Run as the secret_store owner:
--   psql -1 -v ON_ERROR_STOP=1 -f 004_secret_store_operations_api.sql

CREATE FUNCTION secret_store.begin_operation(
  p_operation_type text, p_source_system_id bigint, p_actor_kind text, p_actor_ref text,
  p_target_create_operation_id uuid DEFAULT NULL, p_replaced_create_operation_id uuid DEFAULT NULL)
RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, secret_store AS $fn$
DECLARE
  v_op uuid := gen_random_uuid();
BEGIN
  IF p_operation_type IN ('CREATE', 'REPLACE') AND p_target_create_operation_id IS NOT NULL THEN
    RAISE EXCEPTION 'CREATE/REPLACE take no target binding';
  END IF;
  IF p_operation_type IN ('REVOKE', 'READ') AND p_target_create_operation_id IS NULL THEN
    RAISE EXCEPTION 'REVOKE/READ need a target binding';
  END IF;
  INSERT INTO secret_store.operation_event(operation_id, create_operation_id, source_system_id,
      operation_type, phase, code, replaced_create_operation_id, actor_kind, actor_ref)
  VALUES (v_op, coalesce(p_target_create_operation_id, v_op), p_source_system_id,
      p_operation_type, 'ATTEMPT', 'PERMITTED_ATTEMPT', p_replaced_create_operation_id,
      p_actor_kind, p_actor_ref);
  RETURN v_op;
END;
$fn$;

CREATE FUNCTION secret_store.record_denied_attempt(
  p_operation_type text, p_source_system_id bigint, p_actor_kind text, p_actor_ref text,
  p_target_create_operation_id uuid DEFAULT NULL, p_replaced_create_operation_id uuid DEFAULT NULL)
RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, secret_store AS $fn$
DECLARE
  v_op uuid := gen_random_uuid();
BEGIN
  INSERT INTO secret_store.operation_event(operation_id, create_operation_id, source_system_id,
      operation_type, phase, code, replaced_create_operation_id, actor_kind, actor_ref)
  VALUES (v_op,
      CASE WHEN p_operation_type IN ('CREATE', 'REPLACE') THEN v_op
           ELSE coalesce(p_target_create_operation_id, gen_random_uuid()) END,
      p_source_system_id, p_operation_type, 'ATTEMPT', 'DENIED_ATTEMPT',
      CASE WHEN p_operation_type = 'REPLACE' THEN p_replaced_create_operation_id END,
      p_actor_kind, p_actor_ref);
  RETURN v_op;
END;
$fn$;

CREATE FUNCTION secret_store.register_pending(
  p_operation_id uuid, p_secret_ref uuid, p_profile_code text)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, secret_store AS $fn$
DECLARE
  a record;
BEGIN
  SELECT * INTO a FROM secret_store.operation_event
   WHERE operation_id = p_operation_id AND phase = 'ATTEMPT' AND code = 'PERMITTED_ATTEMPT';
  IF NOT FOUND OR a.operation_type NOT IN ('CREATE', 'REPLACE') THEN
    RAISE EXCEPTION 'No permitted CREATE/REPLACE operation';
  END IF;
  INSERT INTO secret_store.binding(secret_ref, source_system_id, create_operation_id, profile_code)
  VALUES (p_secret_ref, a.source_system_id, p_operation_id, p_profile_code);
END;
$fn$;

CREATE FUNCTION secret_store.finish_operation(p_operation_id uuid, p_succeeded boolean)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, secret_store AS $fn$
DECLARE
  a record;
  v_code text := CASE WHEN p_succeeded THEN 'SUCCEEDED' ELSE 'FAILED' END;
  v_done text;
BEGIN
  IF p_succeeded IS NULL THEN RAISE EXCEPTION 'Outcome required'; END IF;
  SELECT * INTO a FROM secret_store.operation_event
   WHERE operation_id = p_operation_id AND phase = 'ATTEMPT' AND code = 'PERMITTED_ATTEMPT';
  IF NOT FOUND THEN RAISE EXCEPTION 'No permitted operation'; END IF;

  SELECT code INTO v_done FROM secret_store.operation_event
   WHERE operation_id = p_operation_id AND phase = 'OUTCOME';
  IF FOUND THEN
    IF v_done = v_code THEN RETURN; END IF;  -- same outcome repeated (A09)
    RAISE EXCEPTION 'Operation already finished with another outcome';
  END IF;

  INSERT INTO secret_store.operation_event(operation_id, create_operation_id, source_system_id,
      operation_type, phase, code, replaced_create_operation_id, actor_kind, actor_ref)
  VALUES (a.operation_id, a.create_operation_id, a.source_system_id, a.operation_type,
      'OUTCOME', v_code, a.replaced_create_operation_id, a.actor_kind, a.actor_ref);

  IF a.operation_type IN ('CREATE', 'REPLACE') AND NOT p_succeeded THEN
    UPDATE secret_store.binding SET state = 'REVOKED', revoked_at = clock_timestamp()
     WHERE create_operation_id = a.create_operation_id AND state = 'PENDING';
  ELSIF a.operation_type = 'REVOKE' AND p_succeeded THEN
    UPDATE secret_store.binding SET state = 'REVOKED', revoked_at = clock_timestamp()
     WHERE create_operation_id = a.create_operation_id AND state <> 'REVOKED';
    IF NOT FOUND THEN RAISE EXCEPTION 'Binding to revoke not found'; END IF;
  END IF;
END;
$fn$;

CREATE FUNCTION secret_store.activate_binding(
  p_operation_id uuid, p_ciphertext_sha256 text, p_key_id text)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, secret_store AS $fn$
DECLARE
  a record;
BEGIN
  SELECT * INTO a FROM secret_store.operation_event
   WHERE operation_id = p_operation_id AND phase = 'ATTEMPT' AND code = 'PERMITTED_ATTEMPT';
  IF NOT FOUND OR a.operation_type NOT IN ('CREATE', 'REPLACE') THEN
    RAISE EXCEPTION 'No permitted CREATE/REPLACE operation';
  END IF;
  IF a.operation_type = 'REPLACE' THEN
    UPDATE secret_store.binding SET state = 'REVOKED', revoked_at = clock_timestamp()
     WHERE create_operation_id = a.replaced_create_operation_id AND state = 'ACTIVE';
    IF NOT FOUND THEN RAISE EXCEPTION 'Replaced binding is not ACTIVE'; END IF;
  END IF;
  UPDATE secret_store.binding
     SET state = 'ACTIVE', activated_at = clock_timestamp(),
         ciphertext_sha256 = p_ciphertext_sha256, key_id = p_key_id
   WHERE create_operation_id = p_operation_id AND state = 'PENDING';
  IF NOT FOUND THEN RAISE EXCEPTION 'No PENDING binding for this operation'; END IF;
END;
$fn$;

REVOKE ALL ON FUNCTION
  secret_store.begin_operation(text, bigint, text, text, uuid, uuid),
  secret_store.record_denied_attempt(text, bigint, text, text, uuid, uuid),
  secret_store.register_pending(uuid, uuid, text),
  secret_store.finish_operation(uuid, boolean),
  secret_store.activate_binding(uuid, text, text)
FROM PUBLIC;
