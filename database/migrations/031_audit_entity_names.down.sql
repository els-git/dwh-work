-- Rollback of 031 (I13-15): app.audit_log_page gets back its text of package 029 (same signature, rights kept),
-- the two name helpers are dropped. Events of the journal are not touched. Run as dwh_admin:
--   psql -1 -v ON_ERROR_STOP=1 -f 031_audit_entity_names.down.sql
CREATE OR REPLACE FUNCTION app.audit_log_page(p_token_hash text, p_from timestamptz, p_to timestamptz, p_event_type text,
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

DROP FUNCTION app.audit_entity_search(text);
DROP FUNCTION app.audit_entity_name(text, text);
SELECT 'ROLLBACK_031_DONE' AS result;
