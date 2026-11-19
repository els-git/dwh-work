-- I20: switch of the login method of an existing user (LOCAL <-> domain) -- package 025
-- (plan docs/DWH_I20_LDAP_LOGIN_PLAN_APPROVED_20261119.md, section 6.10, decision of the user 19.11.2026).
--
-- app.user_catalog_set_auth_provider(token_hash, user_id, provider, password_hash) -> boolean
--   rules as app.user_catalog_reset_password (inventory 19.11.2026):
--     the administrator session and ADMIN_USER_MANAGE (app.user_admin_session_user), app.can_manage_user,
--     no system account and no SUPERADMIN (also protected by the trigger trg_protect_superadmin);
--   LOCAL -> LDAP / AD: the local password is erased (password_hash NULL), must_change_password false,
--     external_subject NULL (the domain account is tied at the next login, package 024);
--   LDAP / AD -> LOCAL: a temporary password hash (scrypt_v1, made by the API) is required, must_change_password
--     true, external_subject NULL;
--   the same kind of login again (LOCAL -> LOCAL, LDAP <-> AD): nothing changes, true;
--   every switch ends the sessions of the user and is the security event AUTH_PROVIDER_CHANGED (without hashes);
--   the trigger trg_audit_user_account also writes USER_UPDATED.
-- Roles, history and user_id stay. Nothing of 001-024 changes. EXECUTE: dwh_app only.
-- Run as the owner of schema app (dwh_admin):
--   psql -1 -v ON_ERROR_STOP=1 -f 025_auth_provider_switch.sql
DO $pre$
BEGIN
  SET LOCAL search_path = pg_catalog;
  IF to_regprocedure('app.auth_get_directory_user(text)') IS NULL THEN
    RAISE EXCEPTION 'STOP: package 024 is not applied';
  END IF;
  IF to_regprocedure('app.user_admin_session_user(text)') IS NULL
     OR to_regprocedure('app.can_manage_user(bigint, bigint)') IS NULL
     OR to_regclass('app.auth_session') IS NULL THEN
    RAISE EXCEPTION 'STOP: app.user_admin_session_user, app.can_manage_user or app.auth_session not found';
  END IF;
  IF to_regprocedure('app.user_catalog_set_auth_provider(text, bigint, text, text)') IS NOT NULL THEN
    RAISE EXCEPTION 'STOP: 025 is already applied';
  END IF;
END
$pre$;

CREATE FUNCTION app.user_catalog_set_auth_provider(p_token_hash text, p_user_id bigint, p_auth_provider text,
                                                   p_password_hash text)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, audit AS $fn$
DECLARE
  v_actor_user_id bigint;
  v_target app.user_account%ROWTYPE;
  v_provider text := upper(btrim(coalesce(p_auth_provider, '')));
  v_password_hash text := nullif(btrim(coalesce(p_password_hash, '')), '');
  v_revoked_sessions integer := 0;
BEGIN
  v_actor_user_id := app.user_admin_session_user(p_token_hash);

  IF p_user_id IS NULL OR p_user_id <= 0 THEN
    RAISE EXCEPTION 'Target user is required';
  END IF;
  IF v_provider NOT IN ('LOCAL', 'LDAP', 'AD') THEN
    RAISE EXCEPTION 'Invalid authentication provider';
  END IF;
  IF v_provider = 'LOCAL' THEN
    IF v_password_hash IS NULL THEN
      RAISE EXCEPTION 'Password hash is required';
    END IF;
    IF v_password_hash !~ '^scrypt_v1\$32768\$8\$1\$[A-Za-z0-9_-]{22}\$[A-Za-z0-9_-]{43}$' THEN
      RAISE EXCEPTION 'Invalid LOCAL password hash';
    END IF;
  ELSIF v_password_hash IS NOT NULL THEN
    RAISE EXCEPTION 'External authentication account cannot store local password hash';
  END IF;

  SELECT * INTO v_target FROM app.user_account u WHERE u.user_id = p_user_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'User does not exist';
  END IF;
  IF NOT app.can_manage_user(v_actor_user_id, v_target.user_id) THEN
    RAISE EXCEPTION 'User management denied';
  END IF;
  IF v_target.is_system IS TRUE OR v_target.account_level = 'SUPERADMIN' THEN
    RAISE EXCEPTION 'System account authentication is server-side only';
  END IF;
  IF v_target.auth_provider NOT IN ('LOCAL', 'LDAP', 'AD') THEN
    RAISE EXCEPTION 'Invalid authentication provider';
  END IF;

  -- The same kind of login: nothing to switch.
  IF (v_target.auth_provider = 'LOCAL') = (v_provider = 'LOCAL') THEN
    RETURN true;
  END IF;

  UPDATE app.user_account
     SET auth_provider = v_provider,
         password_hash = CASE WHEN v_provider = 'LOCAL' THEN v_password_hash END,
         must_change_password = (v_provider = 'LOCAL'),
         external_subject = NULL,
         updated_at = clock_timestamp()
   WHERE user_id = v_target.user_id;

  UPDATE app.auth_session
     SET revoked_at = clock_timestamp()
   WHERE user_id = v_target.user_id AND revoked_at IS NULL AND expires_at > clock_timestamp();
  GET DIAGNOSTICS v_revoked_sessions = ROW_COUNT;

  PERFORM audit.log_security_event('AUTH_PROVIDER_CHANGED', 'USER_ACCOUNT', v_target.user_id::text,
    'SET_AUTH_PROVIDER', 'SUCCESS',
    jsonb_build_object('target_username', v_target.username, 'old_auth_provider', v_target.auth_provider,
                       'new_auth_provider', v_provider, 'revoked_sessions', v_revoked_sessions));
  RETURN true;
END
$fn$;

REVOKE ALL ON FUNCTION app.user_catalog_set_auth_provider(text, bigint, text, text) FROM PUBLIC;
-- Default privileges of schema app could give the new function to other roles: remove anything but the owner.
DO $grants$
DECLARE
  r record;
BEGIN
  FOR r IN SELECT x.grantee::regrole AS grantee
             FROM pg_proc p CROSS JOIN LATERAL aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) x
            WHERE p.oid = 'app.user_catalog_set_auth_provider(text, bigint, text, text)'::regprocedure
              AND x.grantee <> p.proowner AND x.grantee <> 0 LOOP
    EXECUTE format('REVOKE ALL ON FUNCTION app.user_catalog_set_auth_provider(text, bigint, text, text) FROM %s', r.grantee);
    RAISE NOTICE 'DEFAULT_GRANT_REVOKED app.user_catalog_set_auth_provider %', r.grantee;
  END LOOP;
END
$grants$;
GRANT EXECUTE ON FUNCTION app.user_catalog_set_auth_provider(text, bigint, text, text) TO dwh_app;

SELECT 'MIGRATION_025_DONE' AS result;
