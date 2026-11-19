-- I20: login with a domain account (LDAP / Active Directory) -- package 024
-- (plan docs/DWH_I20_LDAP_LOGIN_PLAN_APPROVED_20261119.md, decisions D1-D9 of 19.11.2026; section 6.3).
--
-- The password of a domain user is checked by the API against the domain controller (D2); the database never
-- sees it. The database gives the API only:
--   app.auth_get_directory_user(username)   the DWH user of login LDAP / AD with that name (case-insensitive),
--                                            with external_subject; the domain user must be created in DWH (D3);
--   app.auth_bind_external_subject(user_id, objectGUID)
--                                            first login: stores the objectGUID in external_subject; later logins:
--                                            only compares (D4). Result: BOUND | MATCH | MISMATCH | IN_USE |
--                                            NOT_DIRECTORY_USER. Every BOUND / MISMATCH / IN_USE is a security
--                                            event of the audit journal.
-- Session creation (app.auth_create_session) and failed logins with a reason (app.auth_log_login_failure, D8)
-- stay as they are. Nothing of 001-023 changes; app.auth_get_local_user stays LOCAL only.
-- EXECUTE on both functions: dwh_app only. Run as the owner of schema app (dwh_admin):
--   psql -1 -v ON_ERROR_STOP=1 -f 024_auth_directory_login.sql
DO $pre$
BEGIN
  SET LOCAL search_path = pg_catalog;
  IF to_regclass('app.user_account') IS NULL
     OR NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = 'app.user_account'::regclass
                     AND attname = 'external_subject' AND NOT attisdropped)
     OR to_regprocedure('app.auth_get_local_user(text)') IS NULL
     OR to_regprocedure('audit.log_security_event(text, text, text, text, text, jsonb)') IS NULL THEN
    RAISE EXCEPTION 'STOP: app.user_account.external_subject, app.auth_get_local_user or audit.log_security_event not found';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'app.user_account'::regclass
                  AND conname = 'ck_user_auth_provider' AND pg_get_constraintdef(oid) LIKE '%LDAP%'
                  AND pg_get_constraintdef(oid) LIKE '%''AD''%') THEN
    RAISE EXCEPTION 'STOP: constraint ck_user_auth_provider with LDAP and AD not found';
  END IF;
  IF to_regprocedure('app.auth_get_directory_user(text)') IS NOT NULL
     OR to_regprocedure('app.auth_bind_external_subject(bigint, text)') IS NOT NULL THEN
    RAISE EXCEPTION 'STOP: 024 is already applied';
  END IF;
END
$pre$;

-- The DWH user of a domain login by name. Inactive users are returned too: the API refuses them with a reason in
-- the journal (D5). The name is compared without case and surrounding spaces, as app.auth_get_local_user does.
CREATE FUNCTION app.auth_get_directory_user(p_username text)
RETURNS TABLE (user_id bigint, username text, display_name text, email text, account_level text, auth_provider text,
               external_subject text, is_active boolean, is_system boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, app AS $fn$
  SELECT u.user_id, u.username, u.display_name, u.email, u.account_level, u.auth_provider, u.external_subject,
         u.is_active, u.is_system
    FROM app.user_account u
   WHERE lower(u.username) = lower(btrim(p_username))
     AND u.auth_provider IN ('LDAP', 'AD')
   LIMIT 1;
$fn$;

-- Ties the DWH user to the domain account (objectGUID, canonical lower-case form). Row locked, so two first logins
-- at once cannot store two GUIDs. A GUID already held by another DWH user is IN_USE (never stored twice).
CREATE FUNCTION app.auth_bind_external_subject(p_user_id bigint, p_subject text)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, audit AS $fn$
DECLARE
  v_user app.user_account%ROWTYPE;
  v_subject text := lower(btrim(coalesce(p_subject, '')));
  v_result text;
BEGIN
  IF v_subject !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
    RAISE EXCEPTION 'Invalid directory subject';
  END IF;
  SELECT * INTO v_user FROM app.user_account WHERE user_id = p_user_id FOR UPDATE;
  IF NOT FOUND OR v_user.auth_provider NOT IN ('LDAP', 'AD') OR v_user.is_active IS DISTINCT FROM true THEN
    RETURN 'NOT_DIRECTORY_USER';
  END IF;
  PERFORM set_config('dwh.actor_user_id', v_user.user_id::text, true);
  PERFORM set_config('dwh.actor_username', v_user.username, true);
  PERFORM set_config('dwh.actor_account_level', v_user.account_level, true);
  IF v_user.external_subject IS NOT NULL THEN
    IF lower(v_user.external_subject) = v_subject THEN
      RETURN 'MATCH';
    END IF;
    v_result := 'MISMATCH';
  ELSIF EXISTS (SELECT 1 FROM app.user_account x WHERE lower(x.external_subject) = v_subject AND x.user_id <> v_user.user_id) THEN
    v_result := 'IN_USE';
  ELSE
    UPDATE app.user_account SET external_subject = v_subject, updated_at = clock_timestamp() WHERE user_id = v_user.user_id;
    v_result := 'BOUND';
  END IF;
  PERFORM audit.log_security_event(
    CASE v_result WHEN 'BOUND' THEN 'DIRECTORY_SUBJECT_BOUND' ELSE 'LDAP_GUID_MISMATCH' END,
    'USER_ACCOUNT', v_user.user_id::text, 'LOGIN',
    CASE v_result WHEN 'BOUND' THEN 'SUCCESS' ELSE 'FAILED' END,
    jsonb_build_object('result', v_result, 'auth_provider', v_user.auth_provider));
  RETURN v_result;
END
$fn$;

REVOKE ALL ON FUNCTION app.auth_get_directory_user(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION app.auth_bind_external_subject(bigint, text) FROM PUBLIC;
-- Default privileges of schema app could give new functions to other roles: remove anything but the owner.
DO $grants$
DECLARE
  r record;
BEGIN
  FOR r IN SELECT p.oid::regprocedure AS fn, x.grantee::regrole AS grantee
             FROM pg_proc p CROSS JOIN LATERAL aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) x
            WHERE p.oid IN ('app.auth_get_directory_user(text)'::regprocedure,
                            'app.auth_bind_external_subject(bigint, text)'::regprocedure)
              AND x.grantee <> p.proowner AND x.grantee <> 0 LOOP
    EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %s', r.fn, r.grantee);
    RAISE NOTICE 'DEFAULT_GRANT_REVOKED % %', r.fn, r.grantee;
  END LOOP;
END
$grants$;
GRANT EXECUTE ON FUNCTION app.auth_get_directory_user(text) TO dwh_app;
GRANT EXECUTE ON FUNCTION app.auth_bind_external_subject(bigint, text) TO dwh_app;

SELECT 'MIGRATION_024_DONE' AS result;
