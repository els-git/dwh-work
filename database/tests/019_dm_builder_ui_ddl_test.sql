-- I19 test of 019: DM marts managed from the UI -- preview, publication that builds aside and swaps,
-- impact on saved reports / AI examples / reference columns with confirmation, failed and lost builds
-- (the published table stays), publication without a build, deletion of a mart.
-- One transaction, ROLLED BACK; session advisory locks released at the end. For E0 and E1, never the working
-- dwh. Test objects ods.i19u_* / dm.i19u_* exist only inside it. Session functions are replaced inside the
-- transaction (as in 018). Run as the schema owner: psql -v ON_ERROR_STOP=1 -f 019_dm_builder_ui_ddl_test.sql
-- Expected last line: ALL_TESTS_PASS (labels U01-U20: 20 checks).
BEGIN;

CREATE FUNCTION pg_temp.expect_fail(label text, stmt text, pattern text) RETURNS void
LANGUAGE plpgsql AS $fn$
BEGIN
  BEGIN
    EXECUTE stmt;
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM !~* pattern THEN RAISE EXCEPTION 'FAIL %: unexpected error (%)', label, SQLERRM; END IF;
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

CREATE OR REPLACE FUNCTION app.integration_admin_session_user(p_token_hash text)
 RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog' AS $function$
BEGIN
  IF p_token_hash = repeat('a', 64) THEN
    PERFORM set_config('dwh.actor_user_id', '9101', true);
    PERFORM set_config('dwh.actor_username', 'i19u_admin', true);
    RETURN 9101;
  END IF;
  RAISE EXCEPTION 'Integration administration access denied';
END;
$function$;

-- What the engine does for one request: claim, begin, fill the target table, finish.
CREATE FUNCTION pg_temp.engine(p_ok boolean, p_worker text DEFAULT 'i19u-worker') RETURNS text
LANGUAGE plpgsql AS $fn$
DECLARE
  c record;
  v_run bigint;
  v_code text;
  v_target text;
BEGIN
  SELECT * INTO c FROM etl.dm_mart_run_request_claim(p_worker);
  IF c.request_id IS NULL THEN RETURN 'EMPTY'; END IF;
  v_run := etl.dm_mart_run_begin(c.request_id, p_worker, c.version_id);
  SELECT m.mart_code INTO v_code FROM meta.dm_mart m WHERE m.mart_id = c.mart_id;
  v_target := CASE WHEN c.trigger_kind = 'PUBLISH' THEN v_code || '__next' ELSE v_code END;
  IF NOT p_ok THEN
    RETURN etl.dm_mart_run_finish(v_run, false, NULL, NULL, 'DM_KEY_NOT_UNIQUE', 'test failure', NULL);
  END IF;
  EXECUTE format('DELETE FROM dm.%I', v_target);
  IF EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = format('dm.%I', v_target)::regclass AND attname = 'name' AND NOT attisdropped) THEN
    EXECUTE format('INSERT INTO dm.%I (id, name, dm_loaded_at) SELECT id, name, now() FROM ods.i19u_req', v_target);
  ELSE
    EXECUTE format('INSERT INTO dm.%I (id, amount, dm_loaded_at) SELECT id, amount, now() FROM ods.i19u_req', v_target);
  END IF;
  RETURN c.trigger_kind || ':' || etl.dm_mart_run_finish(v_run, true, 0, 2, NULL, NULL, NULL);
END
$fn$;

CREATE TABLE ods.i19u_req (id bigint, name text, amount numeric, pin text);
INSERT INTO ods.i19u_req VALUES (1, 'A', 10, 'P1'), (2, 'B', 20, 'P2');

CREATE TEMP TABLE t_id (k text PRIMARY KEY, v bigint);
INSERT INTO t_id SELECT 'entity', app.dm_entity_save(repeat('a', 64), NULL, 'I19U_REQ', 'Request (test)', true);
SELECT app.dm_entity_key_save(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'entity'), 'i19u_req', 'id', true);
INSERT INTO t_id SELECT 'mart', app.dm_mart_save(repeat('a', 64), NULL, 'i19u_mart', 'Requests (test)', NULL,
  'REPORT_VIEW', true, true, true);
INSERT INTO t_id SELECT 'v1', app.dm_mart_version_create(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'mart'));

CREATE FUNCTION pg_temp.def(p_fields text[]) RETURNS jsonb LANGUAGE sql AS $fn$
  SELECT jsonb_build_object('grain_entity_id', (SELECT v FROM t_id WHERE k = 'entity'),
    'sources', jsonb_build_array(jsonb_build_object('alias', 'r', 'table', 'i19u_req', 'kind', 'GRAIN')),
    'fields', jsonb_build_array(jsonb_build_object('name', 'id', 'type', 'bigint', 'is_key', true,
                'sources', jsonb_build_array(jsonb_build_object('alias', 'r', 'column', 'id'))))
              || coalesce((SELECT jsonb_agg(jsonb_build_object('name', f, 'type', CASE f WHEN 'amount' THEN 'numeric' ELSE 'text' END,
                'sources', jsonb_build_array(jsonb_build_object('alias', 'r', 'column', f)))) FROM unnest(p_fields) f), '[]'))
$fn$;

CREATE TEMP TABLE t_res (k text PRIMARY KEY, r jsonb);
INSERT INTO t_res SELECT 'preview0', app.mart_builder_preview(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v1'));
SELECT app.dm_mart_version_save(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v1'), pg_temp.def(ARRAY['name']));
INSERT INTO t_res SELECT 'preview1', app.mart_builder_preview(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v1'));
SELECT pg_temp.ok('U01 preview of an empty draft: no DDL text, problems; of a new mart: build needed, no table, DDL text',
  (SELECT r->'ddl_text' = 'null'::jsonb AND jsonb_array_length(r->'problems') > 0 FROM t_res WHERE k = 'preview0')
  AND (SELECT (r->>'needs_build')::boolean AND NOT (r->>'table_exists')::boolean
          AND r->>'ddl_text' LIKE 'CREATE TABLE dm.i19u_mart (%PRIMARY KEY (id)%'
          AND jsonb_array_length(r->'changes') = 0 FROM t_res WHERE k = 'preview1'));

INSERT INTO t_res SELECT 'pub1', app.mart_builder_publish(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v1'), false);
SELECT pg_temp.ok('U02 publication builds aside: dm.i19u_mart__next, PUBLISH request, version BUILDING',
  (SELECT r->>'status' FROM t_res WHERE k = 'pub1') = 'BUILDING'
  AND to_regclass('dm.i19u_mart__next') IS NOT NULL AND to_regclass('dm.i19u_mart') IS NULL
  AND (SELECT build_status FROM meta.dm_mart_version WHERE version_id = (SELECT v FROM t_id WHERE k = 'v1')) = 'BUILDING'
  AND EXISTS (SELECT 1 FROM etl.dm_mart_run_request WHERE trigger_kind = 'PUBLISH' AND status = 'QUEUED'
               AND version_id = (SELECT v FROM t_id WHERE k = 'v1')));
SELECT pg_temp.expect_fail('U03 version being built cannot change',
  format('SELECT app.dm_mart_version_save(%L, %s, %L::jsonb)', repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v1'),
         pg_temp.def(ARRAY['name'])::text), 'being built');
INSERT INTO t_res SELECT 'run1', to_jsonb(pg_temp.engine(true));
SELECT pg_temp.ok('U04 build and swap: dm.i19u_mart with 2 rows, __next gone, key constraint renamed, v1 PUBLISHED, AI row',
  (SELECT r #>> '{}' FROM t_res WHERE k = 'run1') = 'PUBLISH:SUCCESS'
  AND to_regclass('dm.i19u_mart') IS NOT NULL AND to_regclass('dm.i19u_mart__next') IS NULL
  AND (SELECT count(*) FROM dm.i19u_mart) = 2
  AND EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'dm.i19u_mart'::regclass AND conname = 'i19u_mart_pk')
  AND (SELECT status || ':' || build_status || ':' || ddl_status FROM meta.dm_mart_version
        WHERE version_id = (SELECT v FROM t_id WHERE k = 'v1')) = 'PUBLISHED:NONE:APPLIED'
  AND EXISTS (SELECT 1 FROM meta.ai_report_object WHERE object_name = 'i19u_mart' AND is_active));
SELECT pg_temp.ok('U05 default privileges of dm on the built table: readers and the engine',
  has_table_privilege('dwh_ai_ro', 'dm.i19u_mart', 'SELECT') AND has_table_privilege('dwh_app', 'dm.i19u_mart', 'SELECT')
  AND has_table_privilege('dwh_etl', 'dm.i19u_mart', 'INSERT,DELETE'));

-- A saved report, an AI example and a reference column using the column "name".
INSERT INTO app.report_folder (folder_name, visibility, owner_user_id, owner_username, updated_by_user_id)
VALUES ('i19u folder', 'SHARED', 9101, 'i19u_admin', 9101);
INSERT INTO app.ai_request_log (user_id, username, request_kind, question, status, finished_at)
VALUES (9101, 'i19u_admin', 'REPORT', 'q', 'OK', clock_timestamp());
INSERT INTO app.saved_report (folder_id, title, question, sql_text, object_ids, source_request_id, owner_user_id,
  owner_username, updated_by_user_id)
SELECT f.folder_id, 'Names i19u', 'q', 'SELECT name, count(*) FROM dm.i19u_mart GROUP BY name',
  ARRAY[(SELECT object_id FROM meta.ai_report_object WHERE object_name = 'i19u_mart')],
  (SELECT max(request_id) FROM app.ai_request_log WHERE username = 'i19u_admin'), 9101, 'i19u_admin', 9101
  FROM app.report_folder f WHERE f.folder_name = 'i19u folder';
INSERT INTO meta.ai_example (object_id, question, sql_text)
SELECT object_id, 'ids', 'SELECT id FROM dm.i19u_mart' FROM meta.ai_report_object WHERE object_name = 'i19u_mart';
INSERT INTO meta.ai_value_column (object_id, column_name)
SELECT object_id, 'name' FROM meta.ai_report_object WHERE object_name = 'i19u_mart';

INSERT INTO t_id SELECT 'v2', app.dm_mart_version_create(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'mart'));
SELECT app.dm_mart_version_save(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v2'), pg_temp.def(ARRAY['amount']));
INSERT INTO t_res SELECT 'preview2', app.mart_builder_preview(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v2'));
SELECT pg_temp.ok('U06 preview of removing "name": change REMOVED, the saved report and the reference column, not the example',
  (SELECT r->'changes'->0->>'column_name' = 'name' AND r->'changes'->0->>'change' = 'REMOVED'
          AND jsonb_array_length(r->'impact'->'reports') = 1 AND r->'impact'->'reports'->0->>'title' = 'Names i19u'
          AND jsonb_array_length(r->'impact'->'value_columns') = 1 AND jsonb_array_length(r->'impact'->'examples') = 0
     FROM t_res WHERE k = 'preview2'));
INSERT INTO t_res SELECT 'pub2', app.mart_builder_publish(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v2'), false);
SELECT pg_temp.ok('U07 without confirmation nothing changes (NEEDS_CONFIRM)',
  (SELECT r->>'status' FROM t_res WHERE k = 'pub2') = 'NEEDS_CONFIRM' AND to_regclass('dm.i19u_mart__next') IS NULL
  AND (SELECT build_status FROM meta.dm_mart_version WHERE version_id = (SELECT v FROM t_id WHERE k = 'v2')) = 'NONE'
  AND NOT EXISTS (SELECT 1 FROM etl.dm_mart_run_request WHERE trigger_kind = 'PUBLISH' AND status = 'QUEUED'));
INSERT INTO t_res SELECT 'pub3', app.mart_builder_publish(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v2'), true);
SELECT pg_temp.ok('U08 confirmed: building, the reference column on "name" deactivated',
  (SELECT r->>'status' FROM t_res WHERE k = 'pub3') = 'BUILDING'
  AND NOT (SELECT is_active FROM meta.ai_value_column WHERE column_name = 'name'
            AND object_id = (SELECT object_id FROM meta.ai_report_object WHERE object_name = 'i19u_mart')));
INSERT INTO t_res SELECT 'run2', to_jsonb(pg_temp.engine(false));
SELECT pg_temp.ok('U09 failed build: __next dropped, draft FAILED with the reason, the published table and v1 stay',
  (SELECT r #>> '{}' FROM t_res WHERE k = 'run2') = 'FAILED'
  AND to_regclass('dm.i19u_mart__next') IS NULL AND (SELECT count(*) FROM dm.i19u_mart) = 2
  AND (SELECT build_status || ':' || build_message FROM meta.dm_mart_version WHERE version_id = (SELECT v FROM t_id WHERE k = 'v2'))
      LIKE 'FAILED:DM_KEY_NOT_UNIQUE: test failure%'
  AND (SELECT status FROM meta.dm_mart_version WHERE version_id = (SELECT v FROM t_id WHERE k = 'v1')) = 'PUBLISHED');
INSERT INTO t_res SELECT 'pub4', app.mart_builder_publish(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v2'), true);
INSERT INTO t_res SELECT 'run3', to_jsonb(pg_temp.engine(true));
SELECT pg_temp.ok('U10 retry after the failure: built and swapped, the table has "amount" and no "name"',
  (SELECT r->>'status' FROM t_res WHERE k = 'pub4') = 'BUILDING' AND (SELECT r #>> '{}' FROM t_res WHERE k = 'run3') = 'PUBLISH:SUCCESS'
  AND EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = 'dm.i19u_mart'::regclass AND attname = 'amount' AND NOT attisdropped)
  AND NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = 'dm.i19u_mart'::regclass AND attname = 'name' AND NOT attisdropped)
  AND (SELECT sum(amount) FROM dm.i19u_mart) = 30
  AND (SELECT status FROM meta.dm_mart_version WHERE version_id = (SELECT v FROM t_id WHERE k = 'v1')) = 'SUPERSEDED');

-- Same structure (a filter): published at once, a refresh queued.
INSERT INTO t_id SELECT 'v3', app.dm_mart_version_create(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'mart'));
SELECT app.dm_mart_version_save(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v3'), pg_temp.def(ARRAY['amount'])
  || jsonb_build_object('filters', jsonb_build_array(jsonb_build_object('alias', 'r', 'column', 'amount', 'operator', '>', 'value', 15))));
INSERT INTO t_res SELECT 'pub5', app.mart_builder_publish(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v3'), false);
SELECT pg_temp.ok('U11 same structure: PUBLISHED at once, MANUAL refresh queued, no table built',
  (SELECT r->>'status' FROM t_res WHERE k = 'pub5') = 'PUBLISHED' AND to_regclass('dm.i19u_mart__next') IS NULL
  AND (SELECT status FROM meta.dm_mart_version WHERE version_id = (SELECT v FROM t_id WHERE k = 'v3')) = 'PUBLISHED'
  AND EXISTS (SELECT 1 FROM etl.dm_mart_run_request WHERE trigger_kind = 'MANUAL' AND status = 'QUEUED'
               AND mart_id = (SELECT v FROM t_id WHERE k = 'mart')));
SELECT pg_temp.ok('U12 the queued refresh runs on the published table', pg_temp.engine(true) = 'MANUAL:SUCCESS');

-- A build lost with its dispatcher: the next claim fails it and drops the table being built.
INSERT INTO t_id SELECT 'v4', app.dm_mart_version_create(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'mart'));
SELECT app.dm_mart_version_save(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v4'), pg_temp.def(ARRAY['amount', 'pin']));
SELECT app.mart_builder_publish(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v4'), true);
CREATE TEMP TABLE t_claim AS SELECT * FROM etl.dm_mart_run_request_claim('i19u-lost');
SELECT etl.dm_mart_run_begin((SELECT request_id FROM t_claim), 'i19u-lost', (SELECT version_id FROM t_claim));
SELECT pg_advisory_unlock(hashtext('dwh.dm_mart_run_request'), (SELECT request_id FROM t_claim)::integer);
SELECT count(*) FROM etl.dm_mart_run_request_claim('i19u-worker');
SELECT pg_temp.ok('U13 lost build: request DISPATCHER_LOST, draft FAILED, __next dropped, published table untouched',
  (SELECT result_code FROM etl.dm_mart_run_request WHERE request_id = (SELECT request_id FROM t_claim)) = 'DISPATCHER_LOST'
  AND (SELECT build_status FROM meta.dm_mart_version WHERE version_id = (SELECT v FROM t_id WHERE k = 'v4')) = 'FAILED'
  AND to_regclass('dm.i19u_mart__next') IS NULL AND to_regclass('dm.i19u_mart') IS NOT NULL);
SELECT pg_temp.ok('U14 status view shows the build state of the draft and the mart is not deleted',
  (SELECT draft_build_status || ':' || is_deleted::text FROM etl.v_dm_mart_status
    WHERE mart_id = (SELECT v FROM t_id WHERE k = 'mart')) = 'FAILED:false');
-- A draft with a failed build keeps its run history: it is fixed and published again, not discarded.

-- Deletion of the mart.
INSERT INTO t_res SELECT 'del1', app.mart_builder_delete(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'mart'), false);
SELECT pg_temp.ok('U15 deletion without confirmation: lists the saved report and the example, nothing dropped',
  (SELECT r->>'status' = 'NEEDS_CONFIRM' AND jsonb_array_length(r->'impact'->'reports') = 1
          AND jsonb_array_length(r->'impact'->'examples') = 1 FROM t_res WHERE k = 'del1')
  AND to_regclass('dm.i19u_mart') IS NOT NULL);
INSERT INTO t_res SELECT 'del2', app.mart_builder_delete(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'mart'), true);
SELECT pg_temp.ok('U16 confirmed deletion: table dropped, mart inactive and deleted, AI row and example deactivated',
  (SELECT r->>'status' FROM t_res WHERE k = 'del2') = 'DELETED' AND to_regclass('dm.i19u_mart') IS NULL
  AND (SELECT NOT is_active AND deleted_at IS NOT NULL FROM meta.dm_mart WHERE mart_id = (SELECT v FROM t_id WHERE k = 'mart'))
  AND NOT (SELECT is_active FROM meta.ai_report_object WHERE object_name = 'i19u_mart')
  AND NOT (SELECT is_active FROM meta.ai_example WHERE question = 'ids'
            AND object_id = (SELECT object_id FROM meta.ai_report_object WHERE object_name = 'i19u_mart')));
SELECT pg_temp.expect_fail('U17 deleted mart cannot be changed',
  format('SELECT app.dm_mart_save(%L, %s, NULL, %L, NULL, NULL, false, true, true)', repeat('a', 64),
         (SELECT v FROM t_id WHERE k = 'mart'), 'x'), 'deleted');
SELECT pg_temp.expect_fail('U18 deleted mart gets no new version',
  format('INSERT INTO meta.dm_mart_version (mart_id, version_no, created_by_user_id) VALUES (%s, 99, 9101)',
         (SELECT v FROM t_id WHERE k = 'mart')), 'deleted');
SELECT pg_temp.expect_fail('U19 user without the right cannot publish',
  format('SELECT app.mart_builder_publish(%L, %s, true)', repeat('b', 64), (SELECT v FROM t_id WHERE k = 'v3')), 'access denied');
SELECT pg_temp.ok('U20 builds and deletion are audited; dwh_app may call the builder, dwh_etl may not',
  (SELECT count(*) FROM audit.security_event WHERE event_type IN ('DM_MART_BUILD_STARTED', 'DM_MART_DELETED')
     AND actor_user_id = 9101) = 5
  AND has_function_privilege('dwh_app', 'app.mart_builder_publish(text, bigint, boolean)', 'EXECUTE')
  AND NOT has_function_privilege('dwh_etl', 'app.mart_builder_delete(text, bigint, boolean)', 'EXECUTE')
  AND NOT has_function_privilege('dwh_app', 'meta.mart_builder_ddl(bigint, text)', 'EXECUTE'));

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
SELECT pg_advisory_unlock_all();
