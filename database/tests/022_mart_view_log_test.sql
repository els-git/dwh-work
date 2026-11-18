-- I22 test of 022: role-aware list of marts, log of views and exports, continuation of a view. One transaction,
-- ROLLED BACK. For E0 (sandbox with fixtures) and E1 (restored copy), never the working dwh. The real session
-- check is tested first (M01); then app.saved_report_session_user and app.ai_user_permissions are replaced
-- inside the transaction by test users (as the test of 017). dm.retail_application_history is added to the
-- whitelist with ADMIN_INTEGRATION_MANAGE and stands for a mart a role must not see.
-- Run as the schema owner: psql -v ON_ERROR_STOP=1 -f 022_mart_view_log_test.sql
-- Expected last line: ALL_TESTS_PASS (labels M01-M14: 14 checks).
BEGIN;

CREATE FUNCTION pg_temp.expect_fail(label text, stmt text) RETURNS void
LANGUAGE plpgsql AS $fn$
BEGIN
  BEGIN
    EXECUTE stmt;
  EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'PASS %: rejected (%)', label, SQLERRM;
    RETURN;
  END;
  RAISE EXCEPTION 'FAIL %: statement was accepted', label;
END
$fn$;

CREATE FUNCTION pg_temp.ok(label text, cond boolean) RETURNS void
LANGUAGE plpgsql AS $fn$
BEGIN
  IF cond IS NOT TRUE THEN RAISE EXCEPTION 'FAIL %', label; END IF;
  RAISE NOTICE 'PASS %', label;
END
$fn$;

SELECT pg_temp.expect_fail('M01a real session check: malformed token', $s$SELECT * FROM app.mart_view_list('xyz')$s$);
SELECT pg_temp.expect_fail('M01b real session check: unknown session', $s$SELECT * FROM app.mart_view_list(repeat('f', 64))$s$);
SELECT pg_temp.ok('M01 real session check is fail-closed', true);

DO $setup$
BEGIN
  IF EXISTS (SELECT 1 FROM app.user_account WHERE user_id BETWEEN 900001 AND 900004) THEN
    RAISE EXCEPTION 'FAIL setup: test user ids 900001-900004 are taken';
  END IF;
END
$setup$;

-- Test users: b = viewer with REPORT_VIEW; c = second viewer; d = no REPORT_VIEW.
CREATE OR REPLACE FUNCTION app.ai_user_permissions(p_user_id bigint)
RETURNS SETOF text LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, app AS $fn$
  SELECT x FROM unnest(CASE p_user_id
    WHEN 900001 THEN ARRAY['REPORT_VIEW']
    WHEN 900002 THEN ARRAY['REPORT_VIEW']
    WHEN 900003 THEN ARRAY['AI_ASSISTANT_USE', 'AI_REPORT_RUN'] END) x
$fn$;
CREATE OR REPLACE FUNCTION app.saved_report_session_user(p_token_hash text, p_permission_code text)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app AS $b$
DECLARE v bigint;
BEGIN
  v := CASE p_token_hash WHEN repeat('b', 64) THEN 900001 WHEN repeat('c', 64) THEN 900002
                         WHEN repeat('d', 64) THEN 900003 END;
  IF v IS NULL THEN RAISE EXCEPTION 'Authentication required'; END IF;
  IF NOT EXISTS (SELECT 1 FROM app.ai_user_permissions(v) c WHERE c = p_permission_code) THEN
    RAISE EXCEPTION 'Access denied: %', p_permission_code;
  END IF;
  PERFORM set_config('dwh.actor_user_id', v::text, true);
  PERFORM set_config('dwh.actor_username', 'i22-test-' || v, true);
  RETURN v;
END; $b$;

DO $t$
DECLARE
  b text := repeat('b', 64);
  c text := repeat('c', 64);
  d text := repeat('d', 64);
  v_cur bigint := (SELECT object_id FROM meta.ai_report_object WHERE object_name = 'v_retail_application_current');
  v_hist bigint;
  r record;
  q_view bigint;
  q_export bigint;
  q_denied bigint;
  q_running bigint;
BEGIN
  IF v_cur IS NULL THEN RAISE EXCEPTION 'FAIL setup: whitelist object v_retail_application_current not found'; END IF;
  UPDATE meta.ai_report_object SET required_permission_code = 'REPORT_VIEW', is_active = true WHERE object_id = v_cur;
  INSERT INTO meta.ai_report_object (schema_name, object_name, title, description, required_permission_code)
  VALUES ('dm', 'retail_application_history', 'История заявок', 'test', 'ADMIN_INTEGRATION_MANAGE')
  ON CONFLICT (schema_name, object_name) DO UPDATE SET required_permission_code = 'ADMIN_INTEGRATION_MANAGE', is_active = true
  RETURNING object_id INTO v_hist;

  PERFORM pg_temp.ok('M02 list: the allowed mart is listed, the closed one is not',
    EXISTS (SELECT 1 FROM app.mart_view_list(b) l WHERE l.object_id = v_cur AND l.object_name = 'v_retail_application_current')
    AND NOT EXISTS (SELECT 1 FROM app.mart_view_list(b) l WHERE l.object_id = v_hist));
  PERFORM pg_temp.expect_fail('M03 list without REPORT_VIEW', format('SELECT * FROM app.mart_view_list(%L)', d));

  SELECT * INTO r FROM app.mart_view_begin(b, v_cur, '{"action": "VIEW", "filters": [{"column": "x", "op": "eq", "value": "1"}]}');
  q_view := r.request_id;
  PERFORM pg_temp.ok('M04 begin VIEW: allowed, table returned, RUNNING row with the filters',
    r.is_allowed AND r.schema_name = 'dm' AND r.object_name = 'v_retail_application_current'
    AND EXISTS (SELECT 1 FROM app.ai_request_log l WHERE l.request_id = q_view AND l.request_kind = 'MART_VIEW'
                 AND l.status = 'RUNNING' AND l.user_id = 900001 AND l.object_ids = ARRAY[v_cur]
                 AND l.view_detail->'filters'->0->>'column' = 'x' AND l.saved_report_id IS NULL));

  SELECT * INTO r FROM app.mart_view_begin(b, v_hist, '{"action": "VIEW"}');
  q_denied := r.request_id;
  PERFORM pg_temp.ok('M05 begin on a closed mart: DENIED row, no table',
    NOT r.is_allowed AND r.schema_name IS NULL AND r.object_name IS NULL
    AND EXISTS (SELECT 1 FROM app.ai_request_log l WHERE l.request_id = q_denied AND l.status = 'DENIED'
                 AND l.result_code = 'AI_OBJECT_NOT_ALLOWED' AND l.finished_at IS NOT NULL));

  PERFORM pg_temp.expect_fail('M06a detail without action', format('SELECT * FROM app.mart_view_begin(%L, %s, %L)', b, v_cur, '{"x": 1}'));
  PERFORM pg_temp.expect_fail('M06b detail is not an object', format('SELECT * FROM app.mart_view_begin(%L, %s, %L)', b, v_cur, '[1]'));
  PERFORM pg_temp.expect_fail('M06c detail over 8000 bytes',
    format('SELECT * FROM app.mart_view_begin(%L, %s, %L)', b, v_cur, jsonb_build_object('action', 'VIEW', 'pad', repeat('x', 8100))));
  PERFORM pg_temp.ok('M06 invalid view detail is rejected', true);

  PERFORM pg_temp.expect_fail('M07a unknown mart', format('SELECT * FROM app.mart_view_begin(%L, -1, %L)', b, '{"action": "VIEW"}'));
  UPDATE meta.ai_report_object SET is_active = false WHERE object_id = v_hist;
  PERFORM pg_temp.expect_fail('M07b inactive mart', format('SELECT * FROM app.mart_view_begin(%L, %s, %L)', b, v_hist, '{"action": "VIEW"}'));
  PERFORM pg_temp.ok('M07 unknown or inactive mart is not found', true);

  PERFORM pg_temp.expect_fail('M08a finish of another user''s view',
    format('SELECT app.mart_view_finish(%L, %s, %L, NULL, 5, NULL)', c, q_view, 'OK'));
  PERFORM pg_temp.expect_fail('M08b unsupported status', format('SELECT app.mart_view_finish(%L, %s, %L, NULL, 5, NULL)', b, q_view, 'DENIED'));
  PERFORM app.mart_view_finish(b, q_view, 'OK', NULL, 5, NULL);
  PERFORM pg_temp.ok('M08 finish: own row only, OK with the row count',
    EXISTS (SELECT 1 FROM app.ai_request_log l WHERE l.request_id = q_view AND l.status = 'OK' AND l.row_count = 5
             AND l.finished_at IS NOT NULL));
  PERFORM pg_temp.expect_fail('M09 second finish', format('SELECT app.mart_view_finish(%L, %s, %L, NULL, 5, NULL)', b, q_view, 'OK'));

  SELECT * INTO r FROM app.mart_view_continue(b, q_view);
  PERFORM pg_temp.ok('M10 continue: own finished view, table and filters returned, nothing logged',
    r.is_allowed AND r.object_name = 'v_retail_application_current' AND r.view_detail->'filters'->0->>'value' = '1'
    AND (SELECT count(*) FROM app.ai_request_log WHERE request_kind = 'MART_VIEW') = 2);

  SELECT * INTO r FROM app.mart_view_begin(b, v_cur, '{"action": "VIEW"}');
  q_running := r.request_id;
  SELECT * INTO r FROM app.mart_view_begin(b, v_cur, '{"action": "EXPORT", "format": "CSV"}');
  q_export := r.request_id;
  PERFORM app.mart_view_finish(b, q_export, 'OK', NULL, 5, NULL);
  PERFORM pg_temp.expect_fail('M11a continue of another user''s view', format('SELECT * FROM app.mart_view_continue(%L, %s)', c, q_view));
  PERFORM pg_temp.expect_fail('M11b continue of a running view', format('SELECT * FROM app.mart_view_continue(%L, %s)', b, q_running));
  PERFORM pg_temp.expect_fail('M11c continue of an export', format('SELECT * FROM app.mart_view_continue(%L, %s)', b, q_export));
  PERFORM pg_temp.expect_fail('M11d continue of a denied row', format('SELECT * FROM app.mart_view_continue(%L, %s)', b, q_denied));
  PERFORM pg_temp.ok('M11 continue: only the own finished VIEW row', true);

  UPDATE meta.ai_report_object SET required_permission_code = 'ADMIN_INTEGRATION_MANAGE' WHERE object_id = v_cur;
  SELECT * INTO r FROM app.mart_view_continue(b, q_view);
  PERFORM pg_temp.ok('M12 continue after the permission of the mart changed: not allowed, no table',
    NOT r.is_allowed AND r.object_name IS NULL AND r.view_detail IS NULL);
  UPDATE meta.ai_report_object SET required_permission_code = 'REPORT_VIEW' WHERE object_id = v_cur;

  UPDATE app.ai_request_log SET started_at = clock_timestamp() - interval '13 hours' WHERE request_id = q_view;
  PERFORM pg_temp.expect_fail('M13 continue of a view older than 12 hours', format('SELECT * FROM app.mart_view_continue(%L, %s)', b, q_view));

  PERFORM pg_temp.expect_fail('M14a log refuses an unknown kind',
    $s$INSERT INTO app.ai_request_log (user_id, username, request_kind, question) VALUES (900001, 'x', 'OTHER', 'x')$s$);
  PERFORM pg_temp.expect_fail('M14b MART_VIEW row cannot carry a saved report',
    $s$INSERT INTO app.ai_request_log (user_id, username, request_kind, question, saved_report_id) VALUES (900001, 'x', 'MART_VIEW', 'x', -1)$s$);
  PERFORM pg_temp.ok('M14 log constraints', true);
END
$t$;

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
