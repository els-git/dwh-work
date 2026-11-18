-- I22: viewing DM marts in "Reporting" -- package 022
-- (plan docs/DWH_I22_MART_VIEW_PLAN_APPROVED_20261118.md, decisions V1-V6 of 18.11.2026; V5 = A).
--
-- V2  The API builds the query itself, only for a mart of the whitelist meta.ai_report_object that the role may
--     read (the same rule as saved reports: REPORT_VIEW and the permission of the mart), schema dm only.
--     The database gives the API the table name only after this check; the client never names a table.
-- V5  Every opening of a mart (with its filters and sort) and every export is a row of app.ai_request_log of the
--     new kind MART_VIEW, with the filters in view_detail (jsonb) and the number of rows, never the rows.
--     Further pages of an opened view reuse its row (app.mart_view_continue) and are not logged again.
-- Nothing of 001-021 changes except app.ai_request_log: kind MART_VIEW (the constraint ai_request_log_kind of 017
-- is replaced under the same name, so check 017 stays 8/8) and the column view_detail. New functions are named
-- mart_view_* (check 016 counts app.ai_*, check 017 counts saved_report_* / report_folder_*).
-- Requires package 017. Run as the owner of schemas app, meta (dwh_admin):
--   psql -1 -v ON_ERROR_STOP=1 -f 022_mart_view_log.sql
DO $pre$
BEGIN
  SET LOCAL search_path = pg_catalog;
  IF to_regprocedure('app.saved_report_session_user(text, text)') IS NULL
     OR to_regprocedure('app.saved_report_objects_allowed(bigint, bigint[])') IS NULL THEN
    RAISE EXCEPTION 'STOP: package 017 is not applied';
  END IF;
  IF to_regprocedure('app.mart_view_begin(text, bigint, jsonb)') IS NOT NULL THEN
    RAISE EXCEPTION 'STOP: 022 is already applied';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = 'app.ai_request_log'::regclass
              AND attname = 'view_detail' AND NOT attisdropped) THEN
    RAISE EXCEPTION 'STOP: app.ai_request_log.view_detail already exists';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'app.ai_request_log'::regclass
                  AND conname = 'ai_request_log_kind'
                  AND pg_get_constraintdef(oid) LIKE '%SAVED_REPORT%'
                  AND pg_get_constraintdef(oid) NOT LIKE '%MART_VIEW%') THEN
    RAISE EXCEPTION 'STOP: constraint app.ai_request_log.ai_request_log_kind of 017 not found';
  END IF;
END
$pre$;

-- ------------------------------------------------------------------ request log (app)

ALTER TABLE app.ai_request_log ADD COLUMN view_detail jsonb
  CHECK (view_detail IS NULL OR (jsonb_typeof(view_detail) = 'object' AND octet_length(view_detail::text) <= 8000));
ALTER TABLE app.ai_request_log DROP CONSTRAINT ai_request_log_kind;
ALTER TABLE app.ai_request_log ADD CONSTRAINT ai_request_log_kind
  CHECK (request_kind IN ('HELP', 'REPORT', 'SAVED_REPORT', 'MART_VIEW'));

-- ------------------------------------------------------------------ functions

-- Session user for mart viewing, fail-closed: the session check of 017 with REPORT_VIEW.
CREATE FUNCTION app.mart_view_session_user(p_token_hash text)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app AS $fn$
DECLARE
  v_user bigint;
BEGIN
  v_user := app.saved_report_session_user(p_token_hash, 'REPORT_VIEW');
  PERFORM set_config('dwh.security_source', 'MART_VIEW', true);
  RETURN v_user;
END
$fn$;

-- Marts of the whitelist the session user may open (schema dm, active, existing, permission held).
CREATE FUNCTION app.mart_view_list(p_token_hash text)
RETURNS TABLE (object_id bigint, title text, description text, schema_name text, object_name text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta AS $fn$
DECLARE
  v_user bigint;
BEGIN
  v_user := app.mart_view_session_user(p_token_hash);
  RETURN QUERY
  SELECT o.object_id, o.title, o.description, o.schema_name, o.object_name FROM meta.ai_report_object o
   WHERE o.schema_name = 'dm' AND app.saved_report_objects_allowed(v_user, ARRAY[o.object_id])
   ORDER BY o.title, o.object_id;
END
$fn$;

-- Start of a view or an export: a log row MART_VIEW. Allowed: RUNNING row and the table. Not allowed: the row is
-- finished DENIED at once and no table is returned. p_detail: {"action": "VIEW" | "EXPORT", ...filters, sort,
-- format}, at most 8000 bytes.
CREATE FUNCTION app.mart_view_begin(p_token_hash text, p_object_id bigint, p_detail jsonb)
RETURNS TABLE (request_id bigint, is_allowed boolean, schema_name text, object_name text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta AS $fn$
DECLARE
  v_user bigint;
  o meta.ai_report_object;
  v_ok boolean;
  v_id bigint;
BEGIN
  v_user := app.mart_view_session_user(p_token_hash);
  IF p_detail IS NULL OR jsonb_typeof(p_detail) <> 'object' OR coalesce(p_detail->>'action', '') NOT IN ('VIEW', 'EXPORT')
     OR octet_length(p_detail::text) > 8000 THEN
    RAISE EXCEPTION 'Invalid view detail';
  END IF;
  SELECT * INTO o FROM meta.ai_report_object x WHERE x.object_id = p_object_id;
  IF o.object_id IS NULL OR NOT o.is_active OR o.schema_name <> 'dm' THEN RAISE EXCEPTION 'Mart not found'; END IF;
  v_ok := app.saved_report_objects_allowed(v_user, ARRAY[o.object_id]);
  INSERT INTO app.ai_request_log (user_id, username, request_kind, question, view_detail, status, result_code,
                                  object_ids, row_count, message, finished_at)
  VALUES (v_user, coalesce(nullif(current_setting('dwh.actor_username', true), ''), 'user ' || v_user),
          'MART_VIEW', o.title, p_detail,
          CASE WHEN v_ok THEN 'RUNNING' ELSE 'DENIED' END,
          CASE WHEN v_ok THEN NULL ELSE 'AI_OBJECT_NOT_ALLOWED' END,
          ARRAY[o.object_id], CASE WHEN v_ok THEN NULL ELSE 0 END,
          CASE WHEN v_ok THEN NULL ELSE 'not available to the role' END,
          CASE WHEN v_ok THEN NULL ELSE clock_timestamp() END)
  RETURNING ai_request_log.request_id INTO v_id;
  RETURN QUERY SELECT v_id, v_ok, CASE WHEN v_ok THEN o.schema_name END, CASE WHEN v_ok THEN o.object_name END;
END
$fn$;

-- Further page of an opened view: the user's own finished OK row of action VIEW, started at most 12 hours ago,
-- and the role may still read the mart. Not logged again; returns the filters of the view and the table.
CREATE FUNCTION app.mart_view_continue(p_token_hash text, p_request_id bigint)
RETURNS TABLE (is_allowed boolean, schema_name text, object_name text, view_detail jsonb)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta AS $fn$
DECLARE
  v_user bigint;
  q app.ai_request_log;
  o meta.ai_report_object;
  v_ok boolean;
BEGIN
  v_user := app.mart_view_session_user(p_token_hash);
  SELECT * INTO q FROM app.ai_request_log r WHERE r.request_id = p_request_id;
  IF q.request_id IS NULL OR q.user_id <> v_user OR q.request_kind <> 'MART_VIEW' OR q.status <> 'OK'
     OR q.view_detail->>'action' <> 'VIEW' OR q.started_at < clock_timestamp() - interval '12 hours' THEN
    RAISE EXCEPTION 'View not found or expired';
  END IF;
  SELECT * INTO o FROM meta.ai_report_object x WHERE x.object_id = q.object_ids[1];
  v_ok := o.object_id IS NOT NULL AND o.is_active AND o.schema_name = 'dm'
          AND app.saved_report_objects_allowed(v_user, ARRAY[o.object_id]);
  RETURN QUERY SELECT v_ok, CASE WHEN v_ok THEN o.schema_name END, CASE WHEN v_ok THEN o.object_name END,
                      CASE WHEN v_ok THEN q.view_detail END;
END
$fn$;

-- End of a view or an export: only the user's own RUNNING MART_VIEW row, once.
CREATE FUNCTION app.mart_view_finish(p_token_hash text, p_request_id bigint, p_status text, p_result_code text,
                                     p_row_count bigint, p_message text)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app AS $fn$
DECLARE
  v_user bigint;
  v_status text := upper(btrim(coalesce(p_status, '')));
BEGIN
  v_user := app.mart_view_session_user(p_token_hash);
  IF v_status NOT IN ('OK', 'NO_DATA', 'REFUSED', 'ERROR') THEN RAISE EXCEPTION 'Unsupported request status'; END IF;
  UPDATE app.ai_request_log q
     SET status = v_status, result_code = p_result_code, row_count = p_row_count, message = left(p_message, 4000),
         finished_at = clock_timestamp()
   WHERE q.request_id = p_request_id AND q.user_id = v_user AND q.request_kind = 'MART_VIEW' AND q.status = 'RUNNING';
  IF NOT FOUND THEN RAISE EXCEPTION 'View not found or already finished'; END IF;
END
$fn$;

-- ------------------------------------------------------------------ privileges

REVOKE ALL ON FUNCTION
  app.mart_view_session_user(text),
  app.mart_view_list(text),
  app.mart_view_begin(text, bigint, jsonb),
  app.mart_view_continue(text, bigint),
  app.mart_view_finish(text, bigint, text, text, bigint, text)
FROM PUBLIC;

-- Default privileges of schema app (rule since 005): remove every grant on the new functions except the owner's
-- and report it, then grant only what is intended.
DO $acl$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT 'FUNCTION ' || p.oid::regprocedure::text AS obj, x.grantee
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace, aclexplode(p.proacl) x
     WHERE n.nspname = 'app' AND p.proname LIKE 'mart\_view\_%' AND x.grantee <> p.proowner
  LOOP
    EXECUTE format('REVOKE ALL ON %s FROM %s', r.obj,
                   CASE WHEN r.grantee = 0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    RAISE NOTICE 'DEFAULT_GRANT_REVOKED % FROM %', r.obj,
                 CASE WHEN r.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(r.grantee) END;
  END LOOP;
END
$acl$;

GRANT EXECUTE ON FUNCTION
  app.mart_view_list(text),
  app.mart_view_begin(text, bigint, jsonb),
  app.mart_view_continue(text, bigint),
  app.mart_view_finish(text, bigint, text, text, bigint, text)
TO dwh_app;

SELECT 'MIGRATION_022_DONE' AS result;
