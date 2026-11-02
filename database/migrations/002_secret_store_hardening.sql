-- I02-04 M-SS02: secret_store hardening (review findings G1, G2).
-- G1: TRUNCATE no longer bypasses the append-only/immutable row guards.
-- G2: a secret is bound to a connection profile; at most one ACTIVE secret per
--     (source_system_id, profile_code). REPLACE = revoke old, then activate new,
--     in one transaction. Rows created before this package keep profile_code
--     NULL: a PENDING one can never become ACTIVE. The rule is NOT VALID so
--     that bindings already ACTIVE without a profile (found in dwh: 2 on
--     CRM_RETAIL, 28-29.10.2026) are kept unchanged; they can only be
--     revoked, through an audited operation once the journal supports it.
-- Requires 001_secret_store_baseline. Run as the secret_store owner:
--   psql -1 -v ON_ERROR_STOP=1 -f 002_secret_store_hardening.sql
-- No access is granted to application or ETL roles.

CREATE FUNCTION secret_store.reject_truncate() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, secret_store AS $fn$
BEGIN RAISE EXCEPTION 'TRUNCATE of % prohibited', TG_TABLE_NAME; END;
$fn$;
CREATE TRIGGER binding_truncate_guard BEFORE TRUNCATE ON secret_store.binding
FOR EACH STATEMENT EXECUTE FUNCTION secret_store.reject_truncate();
CREATE TRIGGER event_truncate_guard BEFORE TRUNCATE ON secret_store.operation_event
FOR EACH STATEMENT EXECUTE FUNCTION secret_store.reject_truncate();

-- Profile code follows the project code convention (module/role codes).
-- The future integration_profile (I03) is expected to reference this pair.
ALTER TABLE secret_store.binding ADD COLUMN profile_code text;
ALTER TABLE secret_store.binding ADD CONSTRAINT binding_profile_code_format
  CHECK (profile_code IS NULL OR profile_code ~ '^[A-Z][A-Z0-9_]{0,63}$');
ALTER TABLE secret_store.binding ADD CONSTRAINT binding_active_requires_profile
  CHECK (state <> 'ACTIVE' OR profile_code IS NOT NULL) NOT VALID;
CREATE UNIQUE INDEX binding_one_active_per_profile
  ON secret_store.binding(source_system_id, profile_code) WHERE state = 'ACTIVE';

CREATE FUNCTION secret_store.reject_profile_change() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, secret_store AS $fn$
BEGIN
  IF NEW.profile_code IS DISTINCT FROM OLD.profile_code THEN
    RAISE EXCEPTION 'Binding profile immutable';
  END IF;
  RETURN NEW;
END;
$fn$;
CREATE TRIGGER binding_profile_guard BEFORE UPDATE ON secret_store.binding
FOR EACH ROW EXECUTE FUNCTION secret_store.reject_profile_change();

REVOKE ALL ON FUNCTION secret_store.reject_truncate() FROM PUBLIC;
REVOKE ALL ON FUNCTION secret_store.reject_profile_change() FROM PUBLIC;
