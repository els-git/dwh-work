-- I03 rollback of 005: remove the profile catalog and its API functions.
-- Refuses and changes nothing if any profile exists (dropping the table would
-- lose them). secret_store rows written through the functions stay.
-- Run as the schema owner: psql -v ON_ERROR_STOP=1 -f 005_integration_profile.down.sql
BEGIN;
LOCK TABLE meta.integration_profile IN ACCESS EXCLUSIVE MODE;
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM meta.integration_profile) THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: integration profiles exist';
  END IF;
END
$do$;
DROP FUNCTION app.integration_profile_secret_revoke(text, bigint);
DROP FUNCTION app.integration_profile_secret_complete(text, bigint, uuid, boolean, text, text);
DROP FUNCTION app.integration_profile_secret_begin(text, bigint);
DROP FUNCTION app.integration_profile_secret_scope(bigint, meta.integration_profile, text, uuid);
DROP FUNCTION app.integration_profile_set_status(text, bigint, integer, text);
DROP FUNCTION app.integration_profile_update(text, bigint, integer, text, text, jsonb);
DROP FUNCTION app.integration_profile_create(text, bigint, text, text, text, text, jsonb);
DROP FUNCTION app.integration_profile_list(text, bigint);
DROP TABLE meta.integration_profile;
DROP FUNCTION meta.integration_profile_reject_truncate();
DROP FUNCTION meta.integration_profile_guard();
DROP FUNCTION meta.integration_config_has_secret_key(jsonb);
COMMIT;
