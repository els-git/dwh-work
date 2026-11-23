-- I13-05 (package 029): the administrator views the audit journal and the journal of source password operations
-- through functions of the database; dwh_app gets no rights on the journal tables.
-- Plan: docs/DWH_I13_05_AUDIT_LOG_VIEW_PLAN_APPROVED_20261123.md (Zh1-Zh6 = A; Zh2 - only ADMIN and SUPERADMIN,
-- Zh5 - no export). Inventory 23.11.2026 (plan section 6): the journals already have indexes by time, type,
-- actor and object; nothing changes in tables, triggers or rights of the journals.
--   app.audit_admin_session_user(token)  session of an active ADMIN / SUPERADMIN without a pending password change;
--                                         not executable by dwh_app (used inside the functions below)
--   app.audit_mask_details(jsonb)         values under names like secret / password / token / dsn / cipher /
--                                         credential / private / salt / api_key become "***" (booleans stay),
--                                         a password inside a connection string becomes "***"
--   app.audit_log_page(...)               page of audit.security_event, newest first, filters, masked details;
--                                         the first page of a request is the event AUDIT_LOG_VIEWED (with filters)
--   app.audit_log_filters(token)          values for the filter lists (event types, outcomes, object types)
--   app.secret_journal_page(...)          page of secret_store.operation_event with the source code; the first
--                                         page is the event SECRET_JOURNAL_VIEWED; no secret_ref (not in the journal)
-- EXECUTE for dwh_app only on the last three. Run as the owner of schemas app, audit, secret_store (dwh_admin):
--   psql -1 -v ON_ERROR_STOP=1 -f 029_audit_log_view.sql
DO $pre$
BEGIN
  IF to_regclass('audit.security_event') IS NULL OR to_regclass('secret_store.operation_event') IS NULL
     OR to_regclass('app.auth_session') IS NULL OR to_regclass('app.user_account') IS NULL
     OR to_regclass('meta.source_system') IS NULL THEN
    RAISE EXCEPTION 'STOP: journals, app.auth_session, app.user_account or meta.source_system not found';
  END IF;
  IF to_regprocedure('audit.log_security_event(text,text,text,text,text,jsonb)') IS NULL THEN
    RAISE EXCEPTION 'STOP: audit.log_security_event not found';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'app' AND table_name = 'user_account'
                  AND column_name = 'must_change_password') THEN
    RAISE EXCEPTION 'STOP: app.user_account.must_change_password not found';
  END IF;
  IF to_regprocedure('app.audit_log_page(text,timestamptz,timestamptz,text,text,text,text,bigint,integer)') IS NOT NULL THEN
    RAISE EXCEPTION 'STOP: 029 is already applied';
  END IF;
END
$pre$;

CREATE FUNCTION app.audit_admin_session_user(p_token_hash text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog', 'app' AS $fn$
DECLARE v_user_id bigint; v_username text; v_level text; v_change boolean;
BEGIN
  IF p_token_hash IS NULL OR p_token_hash !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'Invalid authentication session'; END IF;
  SELECT s.user_id, u.username, u.account_level, u.must_change_password INTO v_user_id, v_username, v_level, v_change
    FROM app.auth_session s JOIN app.user_account u ON u.user_id = s.user_id
   WHERE s.token_hash = p_token_hash AND s.revoked_at IS NULL AND s.expires_at > clock_timestamp() AND u.is_active;
  IF v_user_id IS NULL THEN RAISE EXCEPTION 'Authentication required'; END IF;
  IF v_level NOT IN ('SUPERADMIN', 'ADMIN') THEN RAISE EXCEPTION 'Audit log access denied'; END IF;
  IF v_change IS TRUE THEN RAISE EXCEPTION 'Password change required'; END IF;
  PERFORM set_config('dwh.actor_user_id', v_user_id::text, true);
  PERFORM set_config('dwh.actor_username', v_username, true);
  PERFORM set_config('dwh.actor_account_level', v_level, true);
  PERFORM set_config('dwh.security_source', 'AUDIT_VIEW', true);
  RETURN v_user_id;
END
$fn$;

CREATE FUNCTION app.audit_mask_details(p jsonb) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path TO 'pg_catalog' AS $fn$
DECLARE v_out jsonb; v_key text; v_val jsonb;
BEGIN
  IF p IS NULL THEN RETURN NULL; END IF;
  CASE jsonb_typeof(p)
    WHEN 'object' THEN
      v_out := '{}'::jsonb;
      FOR v_key, v_val IN SELECT key, value FROM jsonb_each(p) LOOP
        IF v_key ~* '(secret|passw|pwd|token|dsn|cipher|credential|private|salt|api_?key)'
           AND jsonb_typeof(v_val) NOT IN ('boolean', 'null') THEN
          v_out := v_out || jsonb_build_object(v_key, '***');
        ELSE
          v_out := v_out || jsonb_build_object(v_key, app.audit_mask_details(v_val));
        END IF;
      END LOOP;
      RETURN v_out;
    WHEN 'array' THEN
      RETURN coalesce((SELECT jsonb_agg(app.audit_mask_details(e) ORDER BY n)
                         FROM jsonb_array_elements(p) WITH ORDINALITY AS a(e, n)), '[]'::jsonb);
    WHEN 'string' THEN
      RETURN to_jsonb(regexp_replace(p #>> '{}', '(://[^/\s:@]+:)[^@\s]+@', '\1***@', 'g'));
    ELSE
      RETURN p;
  END CASE;
END
$fn$;

CREATE FUNCTION app.audit_log_page(p_token_hash text, p_from timestamptz, p_to timestamptz, p_event_type text,
                                   p_actor text, p_entity text, p_outcome text, p_before_id bigint, p_limit integer)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog', 'app', 'audit' AS $fn$
DECLARE v_limit integer := least(greatest(coalesce(p_limit, 100), 1), 200); v_rows jsonb; v_count integer;
BEGIN
  PERFORM app.audit_admin_session_user(p_token_hash);
  WITH page AS (
    SELECT e.* FROM audit.security_event e
     WHERE (p_from IS NULL OR e.occurred_at >= p_from) AND (p_to IS NULL OR e.occurred_at < p_to)
       AND (nullif(p_event_type, '') IS NULL OR e.event_type = p_event_type)
       AND (nullif(p_outcome, '') IS NULL OR e.outcome = p_outcome)
       AND (nullif(p_actor, '') IS NULL OR strpos(lower(coalesce(e.actor_username, '')), lower(p_actor)) > 0)
       AND (nullif(p_entity, '') IS NULL OR strpos(lower(coalesce(e.entity_key, '')), lower(p_entity)) > 0
            OR upper(e.entity_type) = upper(p_entity))
       AND (p_before_id IS NULL OR e.security_event_id < p_before_id)
     ORDER BY e.security_event_id DESC LIMIT v_limit + 1)
  SELECT count(*), coalesce(jsonb_agg(jsonb_build_object(
           'id', security_event_id, 'occurred_at', occurred_at, 'actor_user_id', actor_user_id,
           'actor_username', actor_username, 'actor_account_level', actor_account_level, 'event_type', event_type,
           'entity_type', entity_type, 'entity_key', entity_key, 'action', action, 'outcome', outcome,
           'source', source, 'request_id', request_id, 'details', app.audit_mask_details(details))
           ORDER BY security_event_id DESC) FILTER (WHERE rn <= v_limit), '[]'::jsonb)
    INTO v_count, v_rows
    FROM (SELECT page.*, row_number() OVER (ORDER BY security_event_id DESC) AS rn FROM page) x;
  IF p_before_id IS NULL THEN
    PERFORM audit.log_security_event('AUDIT_LOG_VIEWED', 'AUDIT_LOG', 'security_event', 'VIEW', 'SUCCESS',
      jsonb_strip_nulls(jsonb_build_object('from', p_from, 'to', p_to, 'event_type', nullif(p_event_type, ''),
        'actor', nullif(p_actor, ''), 'entity', nullif(p_entity, ''), 'outcome', nullif(p_outcome, ''))));
  END IF;
  RETURN jsonb_build_object('rows', v_rows, 'has_more', v_count > v_limit,
                            'next_before_id', CASE WHEN v_count > v_limit THEN (v_rows -> (v_limit - 1) ->> 'id')::bigint END);
END
$fn$;

CREATE FUNCTION app.audit_log_filters(p_token_hash text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog', 'app', 'audit' AS $fn$
BEGIN
  PERFORM app.audit_admin_session_user(p_token_hash);
  RETURN jsonb_build_object(
    'event_types', (SELECT coalesce(jsonb_agg(t ORDER BY t), '[]') FROM (SELECT DISTINCT event_type AS t FROM audit.security_event) s),
    'outcomes', (SELECT coalesce(jsonb_agg(t ORDER BY t), '[]') FROM (SELECT DISTINCT outcome AS t FROM audit.security_event) s),
    'entity_types', (SELECT coalesce(jsonb_agg(t ORDER BY t), '[]') FROM (SELECT DISTINCT entity_type AS t FROM audit.security_event) s));
END
$fn$;

CREATE FUNCTION app.secret_journal_page(p_token_hash text, p_source_system_id bigint, p_from timestamptz,
                                        p_to timestamptz, p_operation_type text, p_before_id bigint, p_limit integer)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog', 'app', 'audit', 'secret_store', 'meta' AS $fn$
DECLARE v_limit integer := least(greatest(coalesce(p_limit, 100), 1), 200); v_rows jsonb; v_count integer;
BEGIN
  PERFORM app.audit_admin_session_user(p_token_hash);
  WITH page AS (
    SELECT e.event_id, e.occurred_at, e.source_system_id, s.code AS source_code, s.name AS source_name,
           e.operation_type, e.phase, e.code, e.actor_kind, e.actor_ref, e.operation_id
      FROM secret_store.operation_event e JOIN meta.source_system s ON s.source_system_id = e.source_system_id
     WHERE (p_source_system_id IS NULL OR e.source_system_id = p_source_system_id)
       AND (p_from IS NULL OR e.occurred_at >= p_from) AND (p_to IS NULL OR e.occurred_at < p_to)
       AND (nullif(p_operation_type, '') IS NULL OR e.operation_type = p_operation_type)
       AND (p_before_id IS NULL OR e.event_id < p_before_id)
     ORDER BY e.event_id DESC LIMIT v_limit + 1)
  SELECT count(*), coalesce(jsonb_agg(to_jsonb(p) - 'rn' ORDER BY event_id DESC) FILTER (WHERE rn <= v_limit), '[]'::jsonb)
    INTO v_count, v_rows
    FROM (SELECT page.*, row_number() OVER (ORDER BY event_id DESC) AS rn FROM page) p;
  IF p_before_id IS NULL THEN
    PERFORM audit.log_security_event('SECRET_JOURNAL_VIEWED', 'AUDIT_LOG', 'secret_store.operation_event', 'VIEW',
      'SUCCESS', jsonb_strip_nulls(jsonb_build_object('source_system_id', p_source_system_id, 'from', p_from,
        'to', p_to, 'operation_type', nullif(p_operation_type, ''))));
  END IF;
  RETURN jsonb_build_object('rows', v_rows, 'has_more', v_count > v_limit,
                            'next_before_id', CASE WHEN v_count > v_limit THEN (v_rows -> (v_limit - 1) ->> 'event_id')::bigint END);
END
$fn$;

REVOKE ALL ON FUNCTION app.audit_admin_session_user(text), app.audit_mask_details(jsonb),
  app.audit_log_page(text, timestamptz, timestamptz, text, text, text, text, bigint, integer),
  app.audit_log_filters(text),
  app.secret_journal_page(text, bigint, timestamptz, timestamptz, text, bigint, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.audit_log_page(text, timestamptz, timestamptz, text, text, text, text, bigint, integer),
  app.audit_log_filters(text),
  app.secret_journal_page(text, bigint, timestamptz, timestamptz, text, bigint, integer) TO dwh_app;

SELECT 'MIGRATION_029_DONE' AS result;
