-- E0 ONLY (sandbox): objects that 018 relies on besides those of e0_016_ai_stub.sql and package 016.
-- Order of E0: e0_016_ai_stub.sql -> 016 -> this file -> 018. Never run against dwh or a copy of it.
--   schema ods with the privileges read in dwh (I05 step 2, I19 step 2: only dwh_etl reads ods);
--   schema audit with audit.log_security_event (as e0_app_audit_stub.sql);
--   app.integration_admin_session_user / app.integration_run_admin_session_user (as e0_app_audit_stub.sql
--   and e0_015_run_admin_stub.sql); default privileges of dm for dwh_ai_ro (I19 step 2).
-- Run as a superuser; objects are owned by dwh_admin.
SET ROLE dwh_admin;
CREATE SCHEMA ods;
GRANT USAGE ON SCHEMA ods TO dwh_etl;
ALTER DEFAULT PRIVILEGES IN SCHEMA ods GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO dwh_etl;
ALTER DEFAULT PRIVILEGES IN SCHEMA dm GRANT SELECT ON TABLES TO dwh_ai_ro;
-- dwh: new tables of etl/app get default grants (005/010); modelled so that the $acl$ loop of 018 is exercised.
ALTER DEFAULT PRIVILEGES IN SCHEMA etl GRANT SELECT ON TABLES TO dwh_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA etl GRANT INSERT, SELECT, UPDATE, DELETE ON TABLES TO dwh_etl;

CREATE SCHEMA audit;
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

CREATE FUNCTION app.integration_admin_session_user(p_token_hash text)
 RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog', 'app', 'audit' AS $function$
DECLARE v_user_id bigint; v_username text; v_account_level text;
BEGIN
    IF p_token_hash IS NULL OR p_token_hash !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'Invalid authentication session'; END IF;
    SELECT s.user_id, u.username, u.account_level INTO v_user_id, v_username, v_account_level
      FROM app.auth_session s JOIN app.user_account u ON u.user_id = s.user_id
     WHERE s.token_hash = p_token_hash AND s.revoked_at IS NULL AND s.expires_at > clock_timestamp() AND u.is_active = true;
    IF v_user_id IS NULL THEN RAISE EXCEPTION 'Authentication required'; END IF;
    IF v_account_level NOT IN ('SUPERADMIN', 'ADMIN') THEN RAISE EXCEPTION 'Integration administration access denied'; END IF;
    PERFORM set_config('dwh.actor_user_id', v_user_id::text, true);
    PERFORM set_config('dwh.actor_username', v_username, true);
    RETURN v_user_id;
END;
$function$;
REVOKE ALL ON FUNCTION app.integration_admin_session_user(text) FROM PUBLIC;

CREATE FUNCTION app.integration_run_admin_session_user(p_token_hash text)
 RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog', 'app', 'audit' AS $function$
DECLARE v_user_id bigint; v_username text; v_account_level text;
BEGIN
    IF p_token_hash IS NULL OR p_token_hash !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'Invalid authentication session'; END IF;
    SELECT s.user_id, u.username, u.account_level INTO v_user_id, v_username, v_account_level
      FROM app.auth_session s JOIN app.user_account u ON u.user_id = s.user_id
     WHERE s.token_hash = p_token_hash AND s.revoked_at IS NULL AND s.expires_at > clock_timestamp() AND u.is_active = true;
    IF v_user_id IS NULL THEN RAISE EXCEPTION 'Authentication required'; END IF;
    IF v_account_level NOT IN ('SUPERADMIN', 'ADMIN') THEN RAISE EXCEPTION 'Integration run access denied'; END IF;
    PERFORM set_config('dwh.actor_user_id', v_user_id::text, true);
    PERFORM set_config('dwh.actor_username', v_username, true);
    RETURN v_user_id;
END;
$function$;
REVOKE ALL ON FUNCTION app.integration_run_admin_session_user(text) FROM PUBLIC;
