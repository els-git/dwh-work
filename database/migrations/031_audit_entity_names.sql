-- I13-15 (package 031): names of objects in the audit journal view. The name is taken from the catalogs when the
-- journal is shown (way A, decision of the user 23.11.2026); events, tables, triggers and rights of the journal
-- do not change. Plan: docs/DWH_I13_15_AUDIT_ENTITY_NAMES_PLAN_APPROVED_20261123.md (I1-I5 = A).
--   app.audit_entity_name(type, key)   {"name", "deleted"} for SOURCE_OBJECT ("<source code> · <schema>.<object>"),
--                                       SOURCE_SYSTEM (code), DM_MART (code; deleted mart - "deleted": true),
--                                       DM_ENTITY (code), USER_ACCOUNT (login); a number without a catalog row -
--                                       {"name": null, "deleted": true}; other types and text keys - NULL
--   app.audit_entity_search(text)      (type, key) of the objects whose name contains the text (any case)
--   app.audit_log_page(...)            same signature and rights as 029; rows get "entity_name" and
--                                       "entity_deleted"; the "object" filter also finds events by part of the name
-- The two helpers are not executable by PUBLIC or dwh_app (used inside audit_log_page, SECURITY DEFINER).
-- Run as the owner of schemas app, audit, meta (dwh_admin):  psql -1 -v ON_ERROR_STOP=1 -f 031_audit_entity_names.sql
DO $pre$
BEGIN
  IF to_regprocedure('app.audit_log_page(text,timestamptz,timestamptz,text,text,text,text,bigint,integer)') IS NULL THEN
    RAISE EXCEPTION 'STOP: package 029 (app.audit_log_page) not found';
  END IF;
  IF to_regclass('meta.source_object') IS NULL OR to_regclass('meta.source_system') IS NULL
     OR to_regclass('meta.dm_mart') IS NULL OR to_regclass('meta.dm_entity') IS NULL
     OR to_regclass('app.user_account') IS NULL THEN
    RAISE EXCEPTION 'STOP: catalogs meta.source_object / source_system / dm_mart / dm_entity or app.user_account not found';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'meta' AND table_name = 'dm_mart'
                  AND column_name = 'deleted_at') THEN
    RAISE EXCEPTION 'STOP: meta.dm_mart.deleted_at not found (package 019)';
  END IF;
  IF to_regprocedure('app.audit_entity_name(text,text)') IS NOT NULL THEN
    RAISE EXCEPTION 'STOP: 031 is already applied';
  END IF;
END
$pre$;

CREATE FUNCTION app.audit_entity_name(p_type text, p_key text) RETURNS jsonb
LANGUAGE plpgsql STABLE SET search_path TO 'pg_catalog', 'meta', 'app' AS $fn$
DECLARE v_id bigint; v_name text; v_deleted boolean := false; v_found boolean;
BEGIN
  IF p_type NOT IN ('SOURCE_OBJECT', 'SOURCE_SYSTEM', 'DM_MART', 'DM_ENTITY', 'USER_ACCOUNT')
     OR p_key IS NULL OR p_key !~ '^[0-9]{1,18}$' THEN
    RETURN NULL;
  END IF;
  v_id := p_key::bigint;
  CASE p_type
    WHEN 'SOURCE_OBJECT' THEN
      SELECT s.code || ' · ' || o.source_schema || '.' || o.source_object INTO v_name
        FROM meta.source_object o JOIN meta.source_system s ON s.source_system_id = o.source_system_id
       WHERE o.source_object_id = v_id;
    WHEN 'SOURCE_SYSTEM' THEN
      SELECT s.code INTO v_name FROM meta.source_system s WHERE s.source_system_id = v_id;
    WHEN 'DM_MART' THEN
      SELECT m.mart_code, m.deleted_at IS NOT NULL INTO v_name, v_deleted FROM meta.dm_mart m WHERE m.mart_id = v_id;
    WHEN 'DM_ENTITY' THEN
      SELECT e.entity_code INTO v_name FROM meta.dm_entity e WHERE e.entity_id = v_id;
    WHEN 'USER_ACCOUNT' THEN
      SELECT u.username INTO v_name FROM app.user_account u WHERE u.user_id = v_id;
  END CASE;
  v_found := FOUND;
  IF NOT v_found THEN
    RETURN jsonb_build_object('name', NULL, 'deleted', true);
  END IF;
  RETURN jsonb_build_object('name', v_name, 'deleted', coalesce(v_deleted, false));
END
$fn$;

CREATE FUNCTION app.audit_entity_search(p_text text) RETURNS TABLE (entity_type text, entity_key text)
LANGUAGE sql STABLE SET search_path TO 'pg_catalog', 'meta', 'app' AS $fn$
  SELECT 'SOURCE_OBJECT', o.source_object_id::text
    FROM meta.source_object o JOIN meta.source_system s ON s.source_system_id = o.source_system_id
   WHERE strpos(lower(s.code || ' · ' || o.source_schema || '.' || o.source_object), lower(p_text)) > 0
  UNION ALL
  SELECT 'SOURCE_SYSTEM', s.source_system_id::text FROM meta.source_system s WHERE strpos(lower(s.code), lower(p_text)) > 0
  UNION ALL
  SELECT 'DM_MART', m.mart_id::text FROM meta.dm_mart m WHERE strpos(lower(m.mart_code), lower(p_text)) > 0
  UNION ALL
  SELECT 'DM_ENTITY', e.entity_id::text FROM meta.dm_entity e WHERE strpos(lower(e.entity_code), lower(p_text)) > 0
  UNION ALL
  SELECT 'USER_ACCOUNT', u.user_id::text FROM app.user_account u WHERE strpos(lower(u.username), lower(p_text)) > 0
$fn$;

CREATE OR REPLACE FUNCTION app.audit_log_page(p_token_hash text, p_from timestamptz, p_to timestamptz, p_event_type text,
                                   p_actor text, p_entity text, p_outcome text, p_before_id bigint, p_limit integer)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog', 'app', 'audit' AS $fn$
DECLARE v_limit integer := least(greatest(coalesce(p_limit, 100), 1), 200); v_rows jsonb; v_count integer;
BEGIN
  PERFORM app.audit_admin_session_user(p_token_hash);
  WITH found AS (SELECT * FROM app.audit_entity_search(nullif(p_entity, ''))),
  page AS (
    SELECT e.* FROM audit.security_event e
     WHERE (p_from IS NULL OR e.occurred_at >= p_from) AND (p_to IS NULL OR e.occurred_at < p_to)
       AND (nullif(p_event_type, '') IS NULL OR e.event_type = p_event_type)
       AND (nullif(p_outcome, '') IS NULL OR e.outcome = p_outcome)
       AND (nullif(p_actor, '') IS NULL OR strpos(lower(coalesce(e.actor_username, '')), lower(p_actor)) > 0)
       AND (nullif(p_entity, '') IS NULL OR strpos(lower(coalesce(e.entity_key, '')), lower(p_entity)) > 0
            OR upper(e.entity_type) = upper(p_entity)
            OR EXISTS (SELECT 1 FROM found f WHERE f.entity_type = e.entity_type AND f.entity_key = e.entity_key))
       AND (p_before_id IS NULL OR e.security_event_id < p_before_id)
     ORDER BY e.security_event_id DESC LIMIT v_limit + 1)
  SELECT count(*), coalesce(jsonb_agg(jsonb_build_object(
           'id', security_event_id, 'occurred_at', occurred_at, 'actor_user_id', actor_user_id,
           'actor_username', actor_username, 'actor_account_level', actor_account_level, 'event_type', event_type,
           'entity_type', entity_type, 'entity_key', entity_key,
           'entity_name', nm ->> 'name', 'entity_deleted', coalesce((nm ->> 'deleted')::boolean, false),
           'action', action, 'outcome', outcome,
           'source', source, 'request_id', request_id, 'details', app.audit_mask_details(details))
           ORDER BY security_event_id DESC) FILTER (WHERE rn <= v_limit), '[]'::jsonb)
    INTO v_count, v_rows
    FROM (SELECT page.*, row_number() OVER (ORDER BY security_event_id DESC) AS rn,
                 app.audit_entity_name(page.entity_type, page.entity_key) AS nm
            FROM page) x;
  IF p_before_id IS NULL THEN
    PERFORM audit.log_security_event('AUDIT_LOG_VIEWED', 'AUDIT_LOG', 'security_event', 'VIEW', 'SUCCESS',
      jsonb_strip_nulls(jsonb_build_object('from', p_from, 'to', p_to, 'event_type', nullif(p_event_type, ''),
        'actor', nullif(p_actor, ''), 'entity', nullif(p_entity, ''), 'outcome', nullif(p_outcome, ''))));
  END IF;
  RETURN jsonb_build_object('rows', v_rows, 'has_more', v_count > v_limit,
                            'next_before_id', CASE WHEN v_count > v_limit THEN (v_rows -> (v_limit - 1) ->> 'id')::bigint END);
END
$fn$;

REVOKE ALL ON FUNCTION app.audit_entity_name(text, text), app.audit_entity_search(text) FROM PUBLIC;

SELECT 'MIGRATION_031_DONE' AS result;
