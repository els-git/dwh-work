-- I17-09 test of 017: folders, saved reports, role-aware runs, log of runs. One transaction, ROLLED BACK.
-- For E0 (sandbox with fixtures) and E1 (restored copy), never the working dwh. The real session check is tested
-- first (S01); then app.saved_report_session_user and app.ai_user_permissions are replaced inside the transaction
-- by test users (as the test of 016). dm.retail_application_history is added to the whitelist with
-- ADMIN_INTEGRATION_MANAGE and stands for an object a role must not see.
-- Run as the schema owner: psql -v ON_ERROR_STOP=1 -f 017_ai_saved_reports_test.sql
-- Expected last line: ALL_TESTS_PASS (labels S01-S16: 16 checks).
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

SELECT pg_temp.expect_fail('S01a real session check: malformed token', $s$SELECT app.saved_report_session_user('xyz', 'REPORT_VIEW')$s$);
SELECT pg_temp.expect_fail('S01b real session check: unknown session', $s$SELECT app.saved_report_session_user(repeat('f', 64), 'REPORT_VIEW')$s$);
SELECT pg_temp.expect_fail('S01c real session check: unsupported permission', $s$SELECT app.saved_report_session_user(repeat('f', 64), 'ADMIN_USER_MANAGE')$s$);
SELECT pg_temp.ok('S01 real session check is fail-closed', true);

DO $setup$
DECLARE
  v_admin bigint := (SELECT user_id FROM app.user_account
                      WHERE is_active AND account_level IN ('SUPERADMIN', 'ADMIN') ORDER BY user_id LIMIT 1);
BEGIN
  IF v_admin IS NULL THEN RAISE EXCEPTION 'FAIL setup: no active admin user'; END IF;
  IF EXISTS (SELECT 1 FROM app.user_account WHERE user_id BETWEEN 900001 AND 900004) THEN
    RAISE EXCEPTION 'FAIL setup: test user ids 900001-900004 are taken';
  END IF;
  PERFORM set_config('i17.admin', v_admin::text, true);
END
$setup$;

-- Test users: a = administrator (real permissions); b = author (reports, AI reports); c = viewer (REPORT_VIEW only);
-- d = AI reports without REPORT_VIEW; e = second author.
CREATE OR REPLACE FUNCTION app.ai_user_permissions(p_user_id bigint)
RETURNS SETOF text LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, app AS $fn$
  SELECT x FROM unnest(CASE p_user_id
    WHEN 900001 THEN ARRAY['AI_ASSISTANT_USE', 'AI_REPORT_RUN', 'REPORT_VIEW']
    WHEN 900002 THEN ARRAY['REPORT_VIEW']
    WHEN 900003 THEN ARRAY['AI_ASSISTANT_USE', 'AI_REPORT_RUN']
    WHEN 900004 THEN ARRAY['AI_ASSISTANT_USE', 'AI_REPORT_RUN', 'REPORT_VIEW'] END) x
  UNION ALL
  SELECT g.permission_code FROM app.auth_get_permissions(p_user_id) g WHERE p_user_id < 900000
$fn$;
DO $s$
BEGIN
  EXECUTE format($f$
    CREATE OR REPLACE FUNCTION app.saved_report_session_user(p_token_hash text, p_permission_code text)
    RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app AS $b$
    DECLARE v bigint;
    BEGIN
      v := CASE p_token_hash WHEN repeat('a', 64) THEN %s WHEN repeat('b', 64) THEN 900001 WHEN repeat('c', 64) THEN 900002
                             WHEN repeat('d', 64) THEN 900003 WHEN repeat('e', 64) THEN 900004 END;
      IF v IS NULL THEN RAISE EXCEPTION 'Authentication required'; END IF;
      IF NOT EXISTS (SELECT 1 FROM app.ai_user_permissions(v) c WHERE c = p_permission_code) THEN
        RAISE EXCEPTION 'Access denied: %%', p_permission_code;
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
  d text := repeat('d', 64);
  e text := repeat('e', 64);
  v_cur bigint := (SELECT object_id FROM meta.ai_report_object WHERE object_name = 'v_retail_application_current');
  v_hist bigint;
  q_ok bigint;
  q_hist bigint;
  q_denied bigint;
  q_help bigint;
  q_other bigint;
  f_b_personal bigint;
  f_b_shared bigint;
  f_e_personal bigint;
  r_personal bigint;
  r_shared bigint;
  r_closed bigint;
  v_run record;
  x record;
BEGIN
  INSERT INTO meta.ai_report_object (schema_name, object_name, title, description, required_permission_code)
  VALUES ('dm', 'retail_application_history', 'История заявок', 'test', 'ADMIN_INTEGRATION_MANAGE')
  RETURNING object_id INTO v_hist;
  INSERT INTO app.ai_request_log (user_id, username, request_kind, question, status, sql_text, object_ids, row_count, finished_at)
  VALUES (900001, 'i17-test-900001', 'REPORT', 'Заявки по каналам', 'OK',
          'SELECT sales_channel_name_ru, count(*) FROM dm.v_retail_application_current GROUP BY 1', ARRAY[v_cur], 2, clock_timestamp())
  RETURNING request_id INTO q_ok;
  INSERT INTO app.ai_request_log (user_id, username, request_kind, question, status, sql_text, object_ids, row_count, finished_at)
  VALUES (900001, 'i17-test-900001', 'REPORT', 'История', 'OK', 'SELECT count(*) FROM dm.retail_application_history',
          ARRAY[v_hist], 1, clock_timestamp())
  RETURNING request_id INTO q_hist;
  INSERT INTO app.ai_request_log (user_id, username, request_kind, question, status, result_code, finished_at)
  VALUES (900001, 'i17-test-900001', 'REPORT', 'Зарплаты', 'DENIED', 'AI_OBJECT_NOT_ALLOWED', clock_timestamp())
  RETURNING request_id INTO q_denied;
  INSERT INTO app.ai_request_log (user_id, username, request_kind, question, status, finished_at)
  VALUES (900001, 'i17-test-900001', 'HELP', 'Как?', 'OK', clock_timestamp())
  RETURNING request_id INTO q_help;
  INSERT INTO app.ai_request_log (user_id, username, request_kind, question, status, sql_text, object_ids, row_count, finished_at)
  VALUES (900004, 'i17-test-900004', 'REPORT', 'Чужой', 'OK', 'SELECT 1 FROM dm.v_retail_application_current',
          ARRAY[v_cur], 1, clock_timestamp())
  RETURNING request_id INTO q_other;

  f_b_personal := app.report_folder_save(b, NULL, 'Мои отчёты', 'personal');
  f_b_shared := app.report_folder_save(b, NULL, 'Продажи', 'SHARED');
  f_e_personal := app.report_folder_save(e, NULL, 'Мои отчёты', 'PERSONAL');
  PERFORM pg_temp.expect_fail('S02a viewer without AI_REPORT_RUN cannot create a folder',
    format('SELECT app.report_folder_save(%L, NULL, %L, %L)', c, 'x', 'SHARED'));
  PERFORM pg_temp.expect_fail('S02b user without REPORT_VIEW cannot create a folder',
    format('SELECT app.report_folder_save(%L, NULL, %L, %L)', d, 'x', 'SHARED'));
  PERFORM pg_temp.expect_fail('S02c unknown visibility',
    format('SELECT app.report_folder_save(%L, NULL, %L, %L)', b, 'x', 'PUBLIC'));
  PERFORM pg_temp.ok('S02 folders: personal and shared created with owner; same personal name for two owners',
    (SELECT visibility || '|' || owner_user_id || '|' || owner_username FROM app.report_folder WHERE folder_id = f_b_personal)
      = 'PERSONAL|900001|i17-test-900001'
    AND (SELECT visibility FROM app.report_folder WHERE folder_id = f_b_shared) = 'SHARED'
    AND f_e_personal IS NOT NULL);

  PERFORM pg_temp.expect_fail('S03a shared folder names are unique (case-insensitive)',
    format('SELECT app.report_folder_save(%L, NULL, %L, %L)', e, ' продажи ', 'SHARED'));
  PERFORM pg_temp.expect_fail('S03b personal folder names are unique per owner',
    format('SELECT app.report_folder_save(%L, NULL, %L, %L)', b, 'МОИ ОТЧЁТЫ', 'PERSONAL'));
  PERFORM pg_temp.ok('S03 folder name uniqueness', true);

  PERFORM pg_temp.ok('S04 folder list: own personal + shared; others personal hidden; edit flags',
    (SELECT array_agg(folder_id ORDER BY folder_id) FROM app.report_folder_list(b)) = ARRAY[f_b_personal, f_b_shared]
    AND (SELECT array_agg(folder_id ORDER BY folder_id) FROM app.report_folder_list(c)) = ARRAY[f_b_shared]
    AND (SELECT array_agg(folder_id ORDER BY folder_id) FROM app.report_folder_list(a)) = ARRAY[f_b_shared]
    AND (SELECT can_edit FROM app.report_folder_list(a) WHERE folder_id = f_b_shared)
    AND NOT (SELECT can_edit FROM app.report_folder_list(c) WHERE folder_id = f_b_shared)
    AND (SELECT is_own AND can_edit FROM app.report_folder_list(b) WHERE folder_id = f_b_personal));

  r_personal := app.saved_report_save(b, q_ok, f_b_personal, '  ');
  r_shared := app.saved_report_save(b, q_ok, f_b_shared, 'Заявки по каналам продаж');
  r_closed := app.saved_report_save(b, q_hist, f_b_shared, 'История (закрытая)');
  PERFORM pg_temp.expect_fail('S05a another user''s request cannot be saved',
    format('SELECT app.saved_report_save(%L, %s, %s, %L)', b, q_other, f_b_shared, 't'));
  PERFORM pg_temp.expect_fail('S05b a DENIED request cannot be saved',
    format('SELECT app.saved_report_save(%L, %s, %s, %L)', b, q_denied, f_b_shared, 't'));
  PERFORM pg_temp.expect_fail('S05c a HELP request cannot be saved',
    format('SELECT app.saved_report_save(%L, %s, %s, %L)', b, q_help, f_b_shared, 't'));
  PERFORM pg_temp.expect_fail('S05d not into someone else''s personal folder',
    format('SELECT app.saved_report_save(%L, %s, %s, %L)', b, q_ok, f_e_personal, 't'));
  PERFORM pg_temp.expect_fail('S05e viewer without AI_REPORT_RUN cannot save',
    format('SELECT app.saved_report_save(%L, %s, %s, %L)', c, q_ok, f_b_shared, 't'));
  PERFORM pg_temp.ok('S05 saved: SQL, objects and question copied from the request; empty title = question',
    (SELECT sql_text = (SELECT sql_text FROM app.ai_request_log WHERE request_id = q_ok)
            AND object_ids = ARRAY[v_cur] AND title = 'Заявки по каналам' AND source_request_id = q_ok
       FROM app.saved_report WHERE report_id = r_personal));

  PERFORM pg_temp.ok('S06 report list by folder visibility; SQL only when the role may read the objects',
    (SELECT array_agg(report_id ORDER BY report_id) FROM app.saved_report_list(b)) = ARRAY[r_personal, r_shared, r_closed]
    AND (SELECT array_agg(report_id ORDER BY report_id) FROM app.saved_report_list(e)) = ARRAY[r_shared, r_closed]
    AND (SELECT is_allowed AND sql_text IS NOT NULL AND NOT can_edit FROM app.saved_report_list(c) WHERE report_id = r_shared)
    AND (SELECT NOT is_allowed AND sql_text IS NULL FROM app.saved_report_list(c) WHERE report_id = r_closed)
    AND (SELECT is_allowed AND can_edit FROM app.saved_report_list(a) WHERE report_id = r_closed));

  PERFORM pg_temp.ok('S07 allowed objects: viewer gets the current view only, administrator both',
    (SELECT array_agg(object_id) FROM app.saved_report_allowed_objects(c)) = ARRAY[v_cur]
    AND (SELECT count(*) FROM app.saved_report_allowed_objects(a)) = 2);

  SELECT * INTO v_run FROM app.saved_report_run_begin(c, r_shared);
  PERFORM pg_temp.expect_fail('S08a another user cannot finish the run',
    format('SELECT app.saved_report_run_finish(%L, %s, %L, NULL, 1, NULL)', e, v_run.request_id, 'OK'));
  PERFORM app.saved_report_run_finish(c, v_run.request_id, 'ok', NULL, 2, NULL);
  PERFORM pg_temp.expect_fail('S08b a finished run cannot be finished again',
    format('SELECT app.saved_report_run_finish(%L, %s, %L, NULL, 1, NULL)', c, v_run.request_id, 'OK'));
  SELECT * INTO x FROM app.ai_request_log WHERE request_id = v_run.request_id;
  PERFORM pg_temp.ok('S08 viewer runs a shared report: SQL given, log SAVED_REPORT finished OK with SQL',
    v_run.is_allowed AND v_run.sql_text IS NOT NULL AND v_run.object_ids = ARRAY[v_cur]
    AND x.request_kind = 'SAVED_REPORT' AND x.saved_report_id = r_shared AND x.user_id = 900002 AND x.status = 'OK'
    AND x.row_count = 2 AND x.sql_text = v_run.sql_text AND x.finished_at IS NOT NULL);

  SELECT * INTO v_run FROM app.saved_report_run_begin(c, r_closed);
  SELECT * INTO x FROM app.ai_request_log WHERE request_id = v_run.request_id;
  PERFORM pg_temp.ok('S09 closed report: no SQL, the run is logged DENIED at once',
    NOT v_run.is_allowed AND v_run.sql_text IS NULL AND v_run.object_ids IS NULL
    AND x.status = 'DENIED' AND x.result_code = 'AI_OBJECT_NOT_ALLOWED' AND x.sql_text IS NULL AND x.finished_at IS NOT NULL);
  PERFORM pg_temp.expect_fail('S09a someone else''s personal report cannot be run',
    format('SELECT * FROM app.saved_report_run_begin(%L, %s)', e, r_personal));

  PERFORM pg_temp.expect_fail('S10a second author cannot rename another author''s shared report',
    format('SELECT app.saved_report_update(%L, %s, %s, %L)', e, r_shared, f_b_shared, 'x'));
  PERFORM pg_temp.expect_fail('S10b administrator does not see a personal report',
    format('SELECT app.saved_report_update(%L, %s, %s, %L)', a, r_personal, f_b_shared, 'x'));
  PERFORM pg_temp.expect_fail('S10c owner cannot move a report into an invisible folder',
    format('SELECT app.saved_report_update(%L, %s, %s, %L)', b, r_shared, f_e_personal, 'x'));
  PERFORM app.saved_report_update(a, r_shared, f_b_shared, 'Каналы продаж');
  PERFORM app.saved_report_update(b, r_personal, f_b_shared, 'Перенесён');
  PERFORM pg_temp.ok('S10 administrator renames a shared report; owner moves a report',
    (SELECT title || '|' || updated_by_user_id FROM app.saved_report WHERE report_id = r_shared)
      = 'Каналы продаж|' || current_setting('i17.admin')
    AND (SELECT folder_id FROM app.saved_report WHERE report_id = r_personal) = f_b_shared);

  PERFORM pg_temp.expect_fail('S11a administrator cannot change the visibility of another owner''s folder',
    format('SELECT app.report_folder_save(%L, %s, %L, %L)', a, f_b_shared, 'Продажи', 'PERSONAL'));
  PERFORM pg_temp.expect_fail('S11b second author cannot rename a shared folder',
    format('SELECT app.report_folder_save(%L, %s, %L, %L)', e, f_b_shared, 'x', 'SHARED'));
  PERFORM app.report_folder_save(a, f_b_shared, 'Продажи (общие)', 'SHARED');
  PERFORM pg_temp.ok('S11 administrator renames a shared folder',
    (SELECT folder_name FROM app.report_folder WHERE folder_id = f_b_shared) = 'Продажи (общие)');

  PERFORM pg_temp.expect_fail('S12a a folder with reports cannot be deleted',
    format('SELECT app.report_folder_delete(%L, %s)', b, f_b_shared));
  PERFORM pg_temp.expect_fail('S12b viewer cannot delete a report',
    format('SELECT app.saved_report_delete(%L, %s)', c, r_shared));
  PERFORM app.saved_report_delete(b, r_personal);
  PERFORM app.saved_report_delete(a, r_shared);
  PERFORM app.saved_report_delete(b, r_closed);
  PERFORM pg_temp.expect_fail('S12c a deleted report cannot be run',
    format('SELECT * FROM app.saved_report_run_begin(%L, %s)', b, r_shared));
  PERFORM app.report_folder_delete(b, f_b_shared);
  PERFORM pg_temp.ok('S12 delete = deactivation: rows stay, nothing listed',
    NOT EXISTS (SELECT 1 FROM app.saved_report_list(b))
    AND (SELECT array_agg(folder_id) FROM app.report_folder_list(b)) = ARRAY[f_b_personal]
    AND (SELECT count(*) FROM app.saved_report WHERE report_id IN (r_personal, r_shared, r_closed) AND NOT is_active) = 3
    AND EXISTS (SELECT 1 FROM app.report_folder WHERE folder_id = f_b_shared AND NOT is_active));

  PERFORM pg_temp.expect_fail('S13 log: SAVED_REPORT without a report is refused by the table',
    $q$INSERT INTO app.ai_request_log (user_id, username, request_kind, question) VALUES (1, 'x', 'SAVED_REPORT', 'q')$q$);
  PERFORM pg_temp.expect_fail('S14 log: unknown kind is refused by the table',
    $q$INSERT INTO app.ai_request_log (user_id, username, request_kind, question) VALUES (1, 'x', 'OTHER', 'q')$q$);
  PERFORM pg_temp.ok('S15 rows of 016 kinds (HELP/REPORT) are still accepted without saved_report_id',
    (SELECT count(*) FROM app.ai_request_log WHERE request_id IN (q_ok, q_hist, q_denied, q_help, q_other)
        AND saved_report_id IS NULL) = 5);
END
$t$;

-- dwh_app reaches folders and reports only through the functions.
SET LOCAL ROLE dwh_app;
DO $ro$
DECLARE
  stmt text;
  label text;
BEGIN
  FOR label, stmt IN VALUES
    ('S16a dwh_app cannot read app.report_folder', 'SELECT count(*) FROM app.report_folder'),
    ('S16b dwh_app cannot read app.saved_report', 'SELECT count(*) FROM app.saved_report'),
    ('S16c dwh_app cannot call the session helper', 'SELECT app.saved_report_session_user(repeat(''a'', 64), ''REPORT_VIEW'')')
  LOOP
    BEGIN
      EXECUTE stmt;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'PASS %: rejected (%)', label, SQLERRM;
      CONTINUE;
    END;
    RAISE EXCEPTION 'FAIL %: statement was accepted', label;
  END LOOP;
  RAISE NOTICE 'PASS S16 dwh_app reaches saved reports only through the functions';
END
$ro$;
RESET ROLE;

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
