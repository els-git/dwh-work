-- E0 fixture of package 024 (I20): an empty sandbox database with what 024 needs, copied from the structure read
-- on the server by database/diagnostics/i20_step2_auth_inventory.sql (19.11.2026):
--   app.user_account with its columns and CHECK constraints, app.auth_get_local_user (text of the server),
--   audit.security_event and audit.log_security_event (as e0_018_dm_builder_stub.sql).
-- Owner of schemas app, audit: dwh_admin (as on the server). Sandbox only, never the working dwh.
CREATE SCHEMA app AUTHORIZATION dwh_admin;
CREATE SCHEMA audit AUTHORIZATION dwh_admin;
SET ROLE dwh_admin;
REVOKE ALL ON SCHEMA app, audit FROM PUBLIC;
GRANT USAGE ON SCHEMA app TO dwh_app;

CREATE TABLE app.user_account (
  user_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,  -- as in dwh (E1 attempt 1, 19.11.2026)
  username text NOT NULL UNIQUE,
  display_name text,
  email text,
  account_level text NOT NULL,
  auth_provider text NOT NULL DEFAULT 'LOCAL',
  password_hash text,
  external_subject text,
  is_active boolean NOT NULL DEFAULT true,
  is_system boolean NOT NULL DEFAULT false,
  must_change_password boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by_user_id bigint,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_external_auth_no_local_password CHECK (((auth_provider = 'LOCAL'::text) OR (password_hash IS NULL))),
  CONSTRAINT ck_superadmin_identity CHECK (((account_level <> 'SUPERADMIN'::text) OR ((username = 'superadmin'::text)
    AND (auth_provider = 'LOCAL'::text) AND (is_system = true) AND (is_active = true)))),
  CONSTRAINT ck_user_account_level CHECK ((account_level = ANY (ARRAY['SUPERADMIN'::text, 'ADMIN'::text, 'USER'::text]))),
  CONSTRAINT ck_user_auth_provider CHECK ((auth_provider = ANY (ARRAY['LOCAL'::text, 'LDAP'::text, 'AD'::text, 'OIDC'::text]))));
INSERT INTO app.user_account (username, account_level, auth_provider, password_hash, is_system)
VALUES ('superadmin', 'SUPERADMIN', 'LOCAL', 'x', true);

CREATE FUNCTION app.auth_get_local_user(p_username text)
 RETURNS TABLE(user_id bigint, username text, display_name text, email text, account_level text, auth_provider text,
               password_hash text, is_active boolean, is_system boolean, must_change_password boolean)
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO 'pg_catalog', 'app' AS $function$
    SELECT u.user_id, u.username, u.display_name, u.email, u.account_level, u.auth_provider, u.password_hash,
           u.is_active, u.is_system, u.must_change_password
    FROM app.user_account u
    WHERE lower(u.username) = lower(btrim(p_username)) AND u.auth_provider = 'LOCAL'
    LIMIT 1;
$function$;
REVOKE ALL ON FUNCTION app.auth_get_local_user(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.auth_get_local_user(text) TO dwh_app;

CREATE TABLE audit.security_event (
  security_event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, occurred_at timestamptz NOT NULL DEFAULT now(),
  actor_user_id bigint, actor_username text, event_type text NOT NULL, entity_type text NOT NULL, entity_key text,
  action text NOT NULL, outcome text NOT NULL, details jsonb);
CREATE FUNCTION audit.log_security_event(p_event_type text, p_entity_type text, p_entity_key text, p_action text,
  p_outcome text, p_details jsonb)
 RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog', 'audit' AS $function$
DECLARE v_event_id bigint;
BEGIN
    INSERT INTO audit.security_event (actor_user_id, actor_username, event_type, entity_type, entity_key, action, outcome, details)
    VALUES (nullif(current_setting('dwh.actor_user_id', true), '')::bigint, nullif(current_setting('dwh.actor_username', true), ''),
            p_event_type, p_entity_type, p_entity_key, p_action, coalesce(p_outcome, 'SUCCESS'), coalesce(p_details, '{}'::jsonb))
    RETURNING security_event_id INTO v_event_id;
    RETURN v_event_id;
END;
$function$;
REVOKE ALL ON FUNCTION audit.log_security_event(text, text, text, text, text, jsonb) FROM PUBLIC;
RESET ROLE;
