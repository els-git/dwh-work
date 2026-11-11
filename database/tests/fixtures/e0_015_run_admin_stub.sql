-- E0 ONLY (sandbox): app.integration_run_admin_session_user(text), the session check of the CRM run
-- requests in dwh (step 2 of I08: called by app.integration_run_request_create/_cancel; its text was not
-- read). Modelled on app.integration_admin_session_user of e0_app_audit_stub.sql. Never run against dwh or a copy.
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
    PERFORM set_config('dwh.security_source', 'INTEGRATION_CONTROL', true);
    RETURN v_user_id;
END;
$function$;
REVOKE ALL ON FUNCTION app.integration_run_admin_session_user(text) FROM PUBLIC;
