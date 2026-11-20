-- E0 ONLY (sandbox): bindings as created before packages 002-003 in dwh (28-29.10.2026): no profile, no key_id,
-- journal without actor; one PENDING with a FAILED CREATE, two ACTIVE. The NOT VALID constraints of 002/003 and the
-- insert guards are lifted only to place these rows, then restored exactly. Plus one ACTIVE binding with profile
-- SOURCE (must stay untouched). Never run against dwh or a copy of it.
BEGIN;
ALTER TABLE secret_store.binding DROP CONSTRAINT binding_active_requires_profile, DROP CONSTRAINT binding_active_requires_key;
ALTER TABLE secret_store.operation_event DROP CONSTRAINT actor_required;
ALTER TABLE secret_store.binding DISABLE TRIGGER binding_insert_guard;
ALTER TABLE secret_store.operation_event DISABLE TRIGGER event_insert_guard;
INSERT INTO meta.source_system(code, name, source_type) VALUES ('T027_CRM', 'test 027', 'POSTGRESQL');
WITH s AS (SELECT source_system_id AS id FROM meta.source_system WHERE code = 'T027_CRM'),
ops(op, state, created, outcome) AS (VALUES
  ('a0000000-0000-4000-8000-000000000001'::uuid, 'PENDING', timestamptz '2026-10-28 16:04:10', 'FAILED'),
  ('a0000000-0000-4000-8000-000000000002'::uuid, 'ACTIVE',  timestamptz '2026-10-28 17:26:10', 'SUCCEEDED'),
  ('a0000000-0000-4000-8000-000000000003'::uuid, 'ACTIVE',  timestamptz '2026-10-29 14:55:56', 'SUCCEEDED')),
ev AS (
  INSERT INTO secret_store.operation_event(operation_id, create_operation_id, source_system_id, phase, code, occurred_at, operation_type)
  SELECT op, op, s.id, ph, CASE ph WHEN 'ATTEMPT' THEN 'PERMITTED_ATTEMPT' ELSE outcome END, created, 'CREATE'
    FROM ops, s, (VALUES ('ATTEMPT'), ('OUTCOME')) p(ph) RETURNING 1)
INSERT INTO secret_store.binding(secret_ref, source_system_id, create_operation_id, state, ciphertext_sha256, created_at, activated_at)
SELECT gen_random_uuid(), s.id, op, state, CASE WHEN state = 'ACTIVE' THEN repeat('b', 64) END, created,
       CASE WHEN state = 'ACTIVE' THEN created END
  FROM ops, s;
ALTER TABLE secret_store.operation_event ENABLE TRIGGER event_insert_guard;
ALTER TABLE secret_store.binding ENABLE TRIGGER binding_insert_guard;
ALTER TABLE secret_store.operation_event ADD CONSTRAINT actor_required CHECK (actor_kind IS NOT NULL AND actor_ref IS NOT NULL) NOT VALID;
ALTER TABLE secret_store.binding
  ADD CONSTRAINT binding_active_requires_profile CHECK (state <> 'ACTIVE' OR profile_code IS NOT NULL) NOT VALID,
  ADD CONSTRAINT binding_active_requires_key CHECK (state <> 'ACTIVE' OR key_id IS NOT NULL) NOT VALID;
DO $p$
DECLARE v_op uuid; v_src bigint := (SELECT source_system_id FROM meta.source_system WHERE code = 'T027_CRM');
BEGIN
  v_op := secret_store.begin_operation('CREATE', v_src, 'DWH_USER', 'e0', NULL, NULL);
  PERFORM secret_store.register_pending(v_op, gen_random_uuid(), 'SOURCE');
  PERFORM secret_store.finish_operation(v_op, true);
  PERFORM secret_store.activate_binding(v_op, repeat('c', 64), 'K_E0');
END $p$;
COMMIT;
