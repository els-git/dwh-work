-- E0 fixture of package 025 (I20, provider switch), on top of e0_024_auth_stub.sql + package 024.
-- Texts of app.user_admin_session_user, app.protect_superadmin and audit.audit_user_account are those read on the
-- server by database/diagnostics/i20_step6_switch_inventory.sql (19.11.2026). app.has_permission and
-- app.can_manage_user were not inventoried: simple stand-ins (SUPERADMIN manages everyone else, ADMIN manages USER).
-- Sandbox only, never the working dwh.
SET ROLE dwh_admin;
CREATE TABLE app.auth_session (session_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, user_id bigint NOT NULL,
  token_hash text NOT NULL UNIQUE, expires_at timestamptz NOT NULL, revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now());
CREATE FUNCTION app.has_permission(p_user_id bigint, p_permission_code text) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO 'pg_catalog', 'app' AS $f$
  SELECT EXISTS (SELECT 1 FROM app.user_account WHERE user_id = p_user_id AND account_level IN ('SUPERADMIN', 'ADMIN'))
$f$;
CREATE FUNCTION app.can_manage_user(p_actor_user_id bigint, p_target_user_id bigint) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO 'pg_catalog', 'app' AS $f$
  SELECT EXISTS (SELECT 1 FROM app.user_account a, app.user_account t
                  WHERE a.user_id = p_actor_user_id AND t.user_id = p_target_user_id AND a.user_id <> t.user_id
                    AND ((a.account_level = 'SUPERADMIN' AND t.account_level <> 'SUPERADMIN')
                         OR (a.account_level = 'ADMIN' AND t.account_level = 'USER')))
$f$;
CREATE FUNCTION app.user_admin_session_user(p_token_hash text) RETURNS bigint
 LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog', 'app', 'audit' AS $function$
DECLARE v_user_id bigint; v_username text; v_account_level text;
BEGIN
    IF p_token_hash IS NULL OR p_token_hash !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'Invalid authentication session'; END IF;
    SELECT s.user_id, u.username, u.account_level INTO v_user_id, v_username, v_account_level
      FROM app.auth_session s JOIN app.user_account u ON u.user_id = s.user_id
     WHERE s.token_hash = p_token_hash AND s.revoked_at IS NULL AND s.expires_at > clock_timestamp() AND u.is_active = true;
    IF v_user_id IS NULL THEN RAISE EXCEPTION 'Authentication required'; END IF;
    IF v_account_level NOT IN ('SUPERADMIN', 'ADMIN') THEN RAISE EXCEPTION 'User administration access denied'; END IF;
    IF NOT app.has_permission(v_user_id, 'ADMIN_USER_MANAGE') THEN RAISE EXCEPTION 'ADMIN_USER_MANAGE permission required'; END IF;
    PERFORM set_config('dwh.actor_user_id', v_user_id::text, true);
    PERFORM set_config('dwh.actor_username', v_username, true);
    PERFORM set_config('dwh.actor_account_level', v_account_level, true);
    PERFORM set_config('dwh.security_source', 'USER_CATALOG', true);
    RETURN v_user_id;
END;
$function$;
REVOKE ALL ON FUNCTION app.user_admin_session_user(text), app.has_permission(bigint, text), app.can_manage_user(bigint, bigint) FROM PUBLIC;
CREATE FUNCTION app.protect_superadmin() RETURNS trigger LANGUAGE plpgsql AS $function$
BEGIN
    IF TG_OP = 'DELETE' AND OLD.account_level = 'SUPERADMIN' THEN RAISE EXCEPTION 'SUPERADMIN account cannot be deleted'; END IF;
    IF TG_OP = 'UPDATE' AND OLD.account_level = 'SUPERADMIN' THEN
        IF NEW.username IS DISTINCT FROM OLD.username OR NEW.display_name IS DISTINCT FROM OLD.display_name
           OR NEW.email IS DISTINCT FROM OLD.email OR NEW.account_level IS DISTINCT FROM OLD.account_level
           OR NEW.auth_provider IS DISTINCT FROM OLD.auth_provider OR NEW.external_subject IS DISTINCT FROM OLD.external_subject
           OR NEW.is_active IS DISTINCT FROM OLD.is_active OR NEW.is_system IS DISTINCT FROM OLD.is_system
           OR NEW.must_change_password IS DISTINCT FROM OLD.must_change_password OR NEW.created_at IS DISTINCT FROM OLD.created_at
           OR NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id THEN
            RAISE EXCEPTION 'SUPERADMIN account is protected';
        END IF;
        IF NEW.password_hash IS DISTINCT FROM OLD.password_hash THEN
            IF current_user <> 'dwh_admin' OR coalesce(current_setting('dwh.superadmin_recovery', true), 'off') <> 'on' THEN
                RAISE EXCEPTION 'SUPERADMIN password can only be changed through recovery mode';
            END IF;
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$function$;
CREATE TRIGGER trg_protect_superadmin BEFORE DELETE OR UPDATE ON app.user_account FOR EACH ROW EXECUTE FUNCTION app.protect_superadmin();
CREATE FUNCTION audit.audit_user_account() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'audit', 'app' AS $function$
DECLARE v_event_type text;
BEGIN
    IF TG_OP = 'INSERT' THEN
        PERFORM audit.log_security_event('USER_CREATED', 'USER', NEW.username, 'CREATE', 'SUCCESS',
            jsonb_build_object('user_id', NEW.user_id, 'username', NEW.username, 'auth_provider', NEW.auth_provider));
        RETURN NEW;
    END IF;
    IF NOT (NEW.username IS DISTINCT FROM OLD.username OR NEW.display_name IS DISTINCT FROM OLD.display_name
        OR NEW.email IS DISTINCT FROM OLD.email OR NEW.account_level IS DISTINCT FROM OLD.account_level
        OR NEW.auth_provider IS DISTINCT FROM OLD.auth_provider OR NEW.external_subject IS DISTINCT FROM OLD.external_subject
        OR NEW.is_active IS DISTINCT FROM OLD.is_active OR NEW.is_system IS DISTINCT FROM OLD.is_system
        OR NEW.must_change_password IS DISTINCT FROM OLD.must_change_password) THEN
        RETURN NEW;
    END IF;
    v_event_type := 'USER_UPDATED';
    PERFORM audit.log_security_event(v_event_type, 'USER', NEW.username, 'UPDATE', 'SUCCESS',
        jsonb_build_object('user_id', NEW.user_id, 'old', jsonb_build_object('auth_provider', OLD.auth_provider),
                           'new', jsonb_build_object('auth_provider', NEW.auth_provider)));
    RETURN NEW;
END;
$function$;
CREATE TRIGGER trg_audit_user_account AFTER INSERT OR UPDATE ON app.user_account FOR EACH ROW EXECUTE FUNCTION audit.audit_user_account();
CREATE FUNCTION app.user_catalog_reset_password(p_token_hash text, p_user_id bigint, p_password_hash text) RETURNS boolean
 LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog', 'app', 'audit' AS $f$
BEGIN
  RAISE EXCEPTION 'Password reset is available only for LOCAL accounts (E0 stand-in)';
END $f$;
RESET ROLE;
