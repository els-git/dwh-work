-- I17 test of 016: AI permissions, session check, role-aware whitelist, request log, administration,
-- role dwh_ai_ro. One transaction, ROLLED BACK. For E0 (sandbox with fixtures) and E1 (restored copy),
-- never the working dwh. The real session check is tested first (X03-X05); then app.ai_session_user and
-- app.ai_user_permissions are replaced inside the transaction by test users with fixed permission sets
-- (as 008/010-015 replace the session functions). The whitelist object dm.retail_application_history
-- is added with ADMIN_INTEGRATION_MANAGE and stands for a table that a role must not see.
-- Run as the schema owner: psql -v ON_ERROR_STOP=1 -f 016_ai_assistant_test.sql
-- Expected last line: ALL_TESTS_PASS (labels X01-X20: 20 checks).
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

DO $t$
DECLARE
  v_admin bigint := (SELECT user_id FROM app.user_account
                      WHERE is_active AND account_level IN ('SUPERADMIN', 'ADMIN') ORDER BY user_id LIMIT 1);
BEGIN
  IF v_admin IS NULL THEN RAISE EXCEPTION 'FAIL setup: no active admin user'; END IF;
  PERFORM set_config('i17.admin', v_admin::text, true);

  PERFORM pg_temp.ok('X01 three AI permissions: group AI, active, assignable',
    (SELECT count(*) FROM app.permission WHERE permission_code IN ('AI_ASSISTANT_USE', 'AI_REPORT_RUN', 'AI_ADMIN_MANAGE')
        AND permission_group = 'AI' AND is_active AND is_assignable) = 3
    AND (SELECT count(DISTINCT permission_id) FROM app.permission) = (SELECT count(*) FROM app.permission));
  PERFORM pg_temp.ok('X02 administrator gets the AI permissions through app.auth_get_permissions',
    (SELECT count(*) FROM app.auth_get_permissions(v_admin)
      WHERE permission_code IN ('AI_ASSISTANT_USE', 'AI_REPORT_RUN', 'AI_ADMIN_MANAGE')) = 3
    AND (SELECT count(*) FROM app.ai_user_permissions(v_admin) c
          WHERE c IN ('AI_ASSISTANT_USE', 'AI_REPORT_RUN', 'AI_ADMIN_MANAGE')) = 3);
END
$t$;

SELECT pg_temp.expect_fail('X03 real session check: malformed token', $s$SELECT app.ai_session_user('xyz', 'AI_ASSISTANT_USE')$s$);
SELECT pg_temp.expect_fail('X04 real session check: unknown session', $s$SELECT app.ai_session_user(repeat('f', 64), 'AI_ASSISTANT_USE')$s$);
SELECT pg_temp.expect_fail('X05 real session check: not an AI permission', $s$SELECT app.ai_session_user(repeat('f', 64), 'ADMIN_USER_MANAGE')$s$);

-- Test users: a = administrator (real permissions); b = "audit" (AI use and reports, no REPORT_VIEW);
-- c = reporter (AI use, reports, REPORT_VIEW); d = no AI; e = AI use only.
CREATE OR REPLACE FUNCTION app.ai_user_permissions(p_user_id bigint)
RETURNS SETOF text LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, app AS $fn$
  SELECT x FROM unnest(CASE p_user_id
    WHEN 900001 THEN ARRAY['AI_ASSISTANT_USE', 'AI_REPORT_RUN', 'DQ_VIEW']
    WHEN 900002 THEN ARRAY['AI_ASSISTANT_USE', 'AI_REPORT_RUN', 'REPORT_VIEW']
    WHEN 900003 THEN ARRAY['REPORT_VIEW']
    WHEN 900004 THEN ARRAY['AI_ASSISTANT_USE', 'REPORT_VIEW'] END) x
  UNION ALL
  SELECT g.permission_code FROM app.auth_get_permissions(p_user_id) g WHERE p_user_id < 900000
$fn$;
DO $s$
BEGIN
  EXECUTE format($f$
    CREATE OR REPLACE FUNCTION app.ai_session_user(p_token_hash text, p_permission_code text)
    RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app AS $b$
    DECLARE v bigint;
    BEGIN
      v := CASE p_token_hash WHEN repeat('a', 64) THEN %s WHEN repeat('b', 64) THEN 900001 WHEN repeat('c', 64) THEN 900002
                             WHEN repeat('d', 64) THEN 900003 WHEN repeat('e', 64) THEN 900004 END;
      IF v IS NULL THEN RAISE EXCEPTION 'Authentication required'; END IF;
      IF NOT EXISTS (SELECT 1 FROM app.ai_user_permissions(v) c WHERE c = p_permission_code) THEN
        RAISE EXCEPTION 'AI access denied: %%', p_permission_code;
      END IF;
      PERFORM set_config('dwh.actor_user_id', v::text, true);
      PERFORM set_config('dwh.actor_username', 'i17-test-' || v, true);
      RETURN v;
    END; $b$ $f$, current_setting('i17.admin'));
END
$s$;

DO $t$
DECLARE
  a text := repeat('a', 64);
  b text := repeat('b', 64);
  c text := repeat('c', 64);
  e text := repeat('e', 64);
  v_cur bigint := (SELECT object_id FROM meta.ai_report_object WHERE object_name = 'v_retail_application_current');
  v_hist bigint;
  v_id bigint;
  v_rq bigint;
  v_rq2 bigint;
  r record;
BEGIN
  PERFORM pg_temp.ok('X06 seed: v_retail_application_current needs REPORT_VIEW, 8 reference columns',
    (SELECT required_permission_code FROM meta.ai_report_object WHERE object_id = v_cur) = 'REPORT_VIEW'
    AND (SELECT count(*) FROM meta.ai_value_column WHERE object_id = v_cur AND is_active) = 8);

  v_hist := app.ai_report_object_save(a, 'dm', 'retail_application_history', 'История заявок (закрытая)', 'test',
                                      'ADMIN_INTEGRATION_MANAGE', true);
  v_id := app.ai_report_object_save(a, 'dm', 'retail_application_history', 'История заявок', 'test 2',
                                    'ADMIN_INTEGRATION_MANAGE', true);
  PERFORM pg_temp.ok('X07 administrator adds a whitelist object; repeated save changes it, not duplicates',
    v_hist IS NOT NULL AND v_id = v_hist
    AND (SELECT title || '|' || updated_by_user_id FROM meta.ai_report_object WHERE object_id = v_hist)
        = 'История заявок|' || current_setting('i17.admin'));

  PERFORM pg_temp.expect_fail('X08a save: schema other than dm',
    format('SELECT app.ai_report_object_save(%L, %L, %L, %L, NULL, %L, true)', a, 'app', 'user_account', 't', 'REPORT_VIEW'));
  PERFORM pg_temp.expect_fail('X08b save: object missing in dm',
    format('SELECT app.ai_report_object_save(%L, %L, %L, %L, NULL, %L, true)', a, 'dm', 'no_such_view', 't', 'REPORT_VIEW'));
  PERFORM pg_temp.expect_fail('X08c save: unknown required permission',
    format('SELECT app.ai_report_object_save(%L, %L, %L, %L, NULL, %L, true)', a, 'dm', 'v_retail_application_source', 't', 'NO_SUCH_PERMISSION'));
  PERFORM pg_temp.expect_fail('X08d save: user without AI_ADMIN_MANAGE',
    format('SELECT app.ai_report_object_save(%L, %L, %L, %L, NULL, %L, true)', c, 'dm', 'v_retail_application_source', 't', 'REPORT_VIEW'));

  PERFORM pg_temp.ok('X09 reporter: sees the current view in full, the closed table by title only',
    EXISTS (SELECT 1 FROM app.ai_object_access(c) x WHERE x.object_id = v_cur AND x.is_allowed
             AND x.object_name = 'v_retail_application_current' AND x.description IS NOT NULL)
    AND EXISTS (SELECT 1 FROM app.ai_object_access(c) x WHERE x.object_id = v_hist AND NOT x.is_allowed
                 AND x.object_name IS NULL AND x.schema_name IS NULL AND x.description IS NULL
                 AND x.title = 'История заявок'));
  PERFORM pg_temp.ok('X10 "audit" role without REPORT_VIEW: no object is allowed, no names given',
    NOT EXISTS (SELECT 1 FROM app.ai_object_access(b) x WHERE x.is_allowed OR x.object_name IS NOT NULL)
    AND (SELECT count(*) FROM app.ai_object_access(b)) = 2);
  PERFORM pg_temp.ok('X11 administrator: both objects allowed',
    (SELECT count(*) FROM app.ai_object_access(a) x WHERE x.is_allowed) = 2);
  PERFORM pg_temp.expect_fail('X12 user without AI_ASSISTANT_USE gets nothing',
    format('SELECT * FROM app.ai_object_access(%L)', repeat('d', 64)));
  UPDATE meta.ai_report_object SET is_active = false WHERE object_id = v_hist;
  PERFORM pg_temp.ok('X13 inactive whitelist object is not shown',
    NOT EXISTS (SELECT 1 FROM app.ai_object_access(a) x WHERE x.object_id = v_hist));
  UPDATE meta.ai_report_object SET is_active = true WHERE object_id = v_hist;

  v_rq := app.ai_request_begin(e, 'help', 'Как запустить загрузку?', true, 'gemma4-26b-a4b');
  PERFORM pg_temp.expect_fail('X14a REPORT needs AI_REPORT_RUN',
    format('SELECT app.ai_request_begin(%L, %L, %L, false, NULL)', e, 'REPORT', 'q'));
  PERFORM pg_temp.expect_fail('X14b unknown request kind',
    format('SELECT app.ai_request_begin(%L, %L, %L, false, NULL)', b, 'SQL', 'q'));
  PERFORM pg_temp.expect_fail('X14c empty question',
    format('SELECT app.ai_request_begin(%L, %L, %L, false, NULL)', b, 'HELP', ''));
  v_rq2 := app.ai_request_begin(b, 'REPORT', 'Дай зарплатный проект', false, 'gemma4-26b-a4b');
  SELECT * INTO r FROM app.ai_request_log WHERE request_id = v_rq;
  PERFORM pg_temp.ok('X14 request begins RUNNING with user, kind and image flag',
    r.status = 'RUNNING' AND r.user_id = 900004 AND r.request_kind = 'HELP' AND r.image_attached
    AND r.username = 'i17-test-900004' AND r.finished_at IS NULL);

  PERFORM pg_temp.expect_fail('X15a another user cannot finish the request',
    format('SELECT app.ai_request_finish(%L, %s, %L, NULL, NULL, NULL, NULL, NULL)', b, v_rq, 'OK'));
  PERFORM pg_temp.expect_fail('X15b unknown status',
    format('SELECT app.ai_request_finish(%L, %s, %L, NULL, NULL, NULL, NULL, NULL)', b, v_rq2, 'DONE'));
  PERFORM app.ai_request_finish(b, v_rq2, 'denied', 'AI_OBJECT_NOT_ALLOWED', NULL, ARRAY[v_hist], 0, 'not available to the role');
  PERFORM pg_temp.expect_fail('X15c a finished request cannot be finished again',
    format('SELECT app.ai_request_finish(%L, %s, %L, NULL, NULL, NULL, NULL, NULL)', b, v_rq2, 'OK'));
  SELECT * INTO r FROM app.ai_request_log WHERE request_id = v_rq2;
  PERFORM pg_temp.ok('X15 own request finished once: DENIED with code, objects and time',
    r.status = 'DENIED' AND r.result_code = 'AI_OBJECT_NOT_ALLOWED' AND r.object_ids = ARRAY[v_hist]
    AND r.row_count = 0 AND r.finished_at IS NOT NULL);

  PERFORM pg_temp.ok('X16 administrator reads the request log',
    (SELECT count(*) FROM app.ai_request_log_list(a, 10) x WHERE x.request_id IN (v_rq, v_rq2)) = 2);
  PERFORM pg_temp.expect_fail('X16a request log: user without AI_ADMIN_MANAGE',
    format('SELECT * FROM app.ai_request_log_list(%L, 10)', b));
  PERFORM pg_temp.expect_fail('X16b request log: limit out of range',
    format('SELECT * FROM app.ai_request_log_list(%L, 0)', a));

  v_id := app.ai_value_column_save(a, v_cur, 'status_name_ru', 20, true);
  PERFORM pg_temp.ok('X17 reference column: saving an existing one changes it',
    (SELECT max_values FROM meta.ai_value_column WHERE value_column_id = v_id) = 20
    AND (SELECT count(*) FROM meta.ai_value_column WHERE object_id = v_cur) = 8);
  PERFORM pg_temp.expect_fail('X17a reference column missing in the object',
    format('SELECT app.ai_value_column_save(%L, %s, %L, 10, true)', a, v_cur, 'no_such_column'));
  PERFORM pg_temp.expect_fail('X17b reference column: limit out of range',
    format('SELECT app.ai_value_column_save(%L, %s, %L, 0, true)', a, v_cur, 'branch_name_ru'));

  v_id := app.ai_glossary_save(a, NULL, 'Канал ИБ', 'Интернет-банк: sales_channel_code содержит IB', v_cur, true);
  v_rq := app.ai_glossary_save(a, v_id, 'Канал ИБ', 'Интернет-банк', v_cur, true);
  PERFORM pg_temp.ok('X18 glossary: add, change',
    v_rq = v_id
    AND (SELECT meaning FROM meta.ai_glossary WHERE term_id = v_id) = 'Интернет-банк');
  PERFORM pg_temp.expect_fail('X18a glossary: unknown id',
    format('SELECT app.ai_glossary_save(%L, -1, %L, %L, NULL, true)', a, 't', 'm'));
  v_id := app.ai_example_save(a, NULL, v_cur, 'Количество заявок по каналам',
    'SELECT sales_channel_name_ru, count(*) FROM dm.v_retail_application_current GROUP BY 1', true);
  v_rq := app.ai_example_save(a, v_id, v_cur, 'Количество заявок по каналам продаж',
      'SELECT sales_channel_name_ru, count(*) FROM dm.v_retail_application_current GROUP BY 1 ORDER BY 2 DESC', false);
  PERFORM pg_temp.ok('X19 example: add and change',
    v_rq = v_id
    AND NOT (SELECT is_active FROM meta.ai_example WHERE example_id = v_id));
  PERFORM pg_temp.expect_fail('X19a example: user without AI_ADMIN_MANAGE',
    format('SELECT app.ai_example_save(%L, NULL, %s, %L, %L, true)', c, v_cur, 'q', 'SELECT 1'));
END
$t$;

-- dwh_ai_ro: reads dm, nothing else (plain DO block: the role does not need the temporary test functions).
SET LOCAL ROLE dwh_ai_ro;
DO $ro$
DECLARE
  n bigint;
  stmt text;
  label text;
BEGIN
  SELECT count(*) INTO n FROM dm.v_retail_application_current;
  RAISE NOTICE 'PASS X20 dwh_ai_ro reads dm.v_retail_application_current';
  FOR label, stmt IN VALUES
    ('X20a dwh_ai_ro cannot read app.user_account', 'SELECT count(*) FROM app.user_account'),
    ('X20b dwh_ai_ro cannot read meta.ai_report_object', 'SELECT count(*) FROM meta.ai_report_object'),
    ('X20c dwh_ai_ro cannot write to dm', 'DELETE FROM dm.retail_application_history WHERE false'),
    ('X20d dwh_ai_ro cannot call AI functions', 'SELECT count(*) FROM app.ai_object_access(repeat(''a'', 64))')
  LOOP
    BEGIN
      EXECUTE stmt;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'PASS %: rejected (%)', label, SQLERRM;
      CONTINUE;
    END;
    RAISE EXCEPTION 'FAIL %: statement was accepted', label;
  END LOOP;
END
$ro$;
RESET ROLE;

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
