-- I06-05 test of 012: ODS configuration (white list, publish checks, immutability, proposals)
-- and the ODS step of runs (begin/finish, history, errors). One transaction, ROLLED BACK;
-- session advisory locks released explicitly at the end.
-- For E0 (sandbox with fixtures) and E1 (restored copy), never the working dwh.
-- app.integration_admin_session_user() is replaced inside the transaction (as in 008/010/011).
-- Run as the schema owner: psql -v ON_ERROR_STOP=1 -f 012_object_ods_config_test.sql
-- Expected last line: ALL_TESTS_PASS (labels T01-T22 and T21b: 23 checks).
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

-- Test objects: meta.source_object has more columns in dwh than in the E0 fixture,
-- so the INSERT fills every NOT NULL column without a default by name or type.
CREATE FUNCTION pg_temp.make_object(p_system bigint, p_name text, p_active boolean) RETURNS bigint
LANGUAGE plpgsql AS $fn$
DECLARE
  cols text := 'source_system_id, source_schema, source_object, is_active';
  vals text := format('%s, %L, %L, %L', p_system, 'i0605_test', p_name, p_active);
  a record;
  v bigint;
BEGIN
  FOR a IN SELECT att.attname, format_type(att.atttypid, att.atttypmod) AS typ
             FROM pg_attribute att
            WHERE att.attrelid = 'meta.source_object'::regclass AND att.attnum > 0 AND NOT att.attisdropped
              AND att.attnotnull AND att.attidentity = '' AND att.attgenerated = ''
              AND NOT EXISTS (SELECT 1 FROM pg_attrdef d WHERE d.adrelid = att.attrelid AND d.adnum = att.attnum)
              AND att.attname NOT IN ('source_object_id', 'source_system_id', 'source_schema', 'source_object', 'is_active')
  LOOP
    cols := cols || ', ' || quote_ident(a.attname);
    vals := vals || ', ' || CASE
      WHEN a.attname = 'object_type' THEN quote_literal('TABLE')
      WHEN a.attname = 'load_strategy' THEN quote_literal('FULL')
      WHEN a.typ IN ('integer', 'bigint', 'smallint', 'numeric') THEN '100'
      WHEN a.typ = 'boolean' THEN 'false'
      WHEN a.typ LIKE 'timestamp%' OR a.typ = 'date' THEN 'now()'
      WHEN a.typ = 'jsonb' OR a.typ = 'json' THEN quote_literal('{}')
      ELSE quote_literal('i0605t_' || p_system || '_' || p_name) END;
  END LOOP;
  EXECUTE format('INSERT INTO meta.source_object (%s) VALUES (%s) RETURNING source_object_id', cols, vals) INTO v;
  RETURN v;
END
$fn$;

DO $setup$
DECLARE
  v_admin bigint := (SELECT user_id FROM app.user_account
                      WHERE is_active AND account_level IN ('SUPERADMIN', 'ADMIN') ORDER BY user_id LIMIT 1);
BEGIN
  IF v_admin IS NULL THEN RAISE EXCEPTION 'FAIL setup: no active admin user'; END IF;
  EXECUTE format($f$
    CREATE OR REPLACE FUNCTION app.integration_admin_session_user(p_token_hash text)
     RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog', 'app', 'audit' AS $b$
    DECLARE v bigint;
    BEGIN
      v := CASE p_token_hash WHEN repeat('a', 64) THEN %s WHEN repeat('b', 64) THEN 999999999 END;
      IF v IS NULL THEN RAISE EXCEPTION 'Authentication required'; END IF;
      PERFORM set_config('dwh.actor_user_id', v::text, true);
      PERFORM set_config('dwh.security_source', 'INTEGRATION_CONTROL', true);
      RETURN v;
    END; $b$ $f$, v_admin);
END
$setup$;

-- A source with a READY connection.
CREATE FUNCTION pg_temp.source(p_code text) RETURNS bigint
LANGUAGE plpgsql AS $fn$
DECLARE v bigint;
BEGIN
  INSERT INTO meta.source_system(code, name, source_type, description, is_active, created_at, updated_at)
  VALUES (p_code, 'I06-05 test source', 'POSTGRESQL', NULL, true, now(), now()) RETURNING source_system_id INTO v;
  INSERT INTO meta.source_connection(source_system_id, source_kind, connection_config, status, created_by, updated_by,
      last_test_at, last_test_ok, last_test_code, last_test_by)
  VALUES (v, 'DATABASE', '{"host": "db", "port": 5432, "database": "src", "username": "reader"}', 'READY', 1, 1,
      now(), true, 'OK', 1);
  RETURN v;
END
$fn$;

CREATE FUNCTION pg_temp.f(p_col text) RETURNS jsonb
LANGUAGE sql AS $fn$
  SELECT jsonb_build_object('target_column', p_col, 'source_column', p_col, 'source_type', 'TEXT',
    'target_type', 'TEXT', 'conversion', 'NONE', 'write_policy', 'AUTHORITATIVE', 'allow_explicit_null', false) $fn$;

-- ODS field: transformation, STG column (NULL for CONSTANT/LOAD_RUN_ID/LOADED_AT), target type, value, policy.
CREATE FUNCTION pg_temp.o(p_target text, p_transform text, p_stg text, p_type text, p_param text DEFAULT NULL,
  p_policy text DEFAULT 'AUTHORITATIVE') RETURNS jsonb
LANGUAGE sql AS $fn$
  SELECT jsonb_build_object('target_column', p_target, 'transform', p_transform, 'stg_column', p_stg,
    'stg_type', CASE WHEN p_stg IS NULL THEN NULL ELSE 'TEXT' END, 'target_type', p_type, 'conversion', 'NONE',
    'param', p_param, 'write_policy', p_policy, 'allow_explicit_null', false) $fn$;

CREATE FUNCTION pg_temp.odoc(p_table text, p_fields jsonb, p_key jsonb) RETURNS jsonb
LANGUAGE sql AS $fn$ SELECT jsonb_build_object('target_table', p_table, 'fields', p_fields, 'key', p_key) $fn$;

CREATE FUNCTION pg_temp.good() RETURNS jsonb
LANGUAGE sql AS $fn$
  SELECT jsonb_build_array(pg_temp.o('id', 'COPY', 'id', 'TEXT'), pg_temp.o('pin', 'TRIM', 'pin', 'TEXT'),
    pg_temp.o('name', 'EMPTY_TO_NULL', 'name', 'TEXT'),
    pg_temp.o('birth', 'DATE_FROM_TEXT', 'birth', 'DATE', 'YYYY-MM-DD'),
    pg_temp.o('channel', 'DEFAULT', 'channel', 'TEXT', 'WEB'), pg_temp.o('source_code', 'CONSTANT', NULL, 'TEXT', 'OC'),
    pg_temp.o('loaded_at', 'LOADED_AT', NULL, 'TIMESTAMPTZ', NULL, 'INSERT_ONLY'),
    pg_temp.o('load_run_id', 'LOAD_RUN_ID', NULL, 'BIGINT', NULL, 'INSERT_ONLY')) $fn$;

CREATE FUNCTION pg_temp.save(p_object bigint, p_rev integer, p_doc jsonb) RETURNS text
LANGUAGE sql AS $fn$
  SELECT format('SELECT app.integration_ods_config_save_draft(%L, %s, %s, %L)', repeat('a', 64), p_object,
                coalesce(p_rev::text, 'NULL'), p_doc) $fn$;

DO $t$
DECLARE
  tok text := repeat('a', 64);
  src bigint;
  o1 bigint;
  o2 bigint;
  rev integer;
  orev integer;
  ovid bigint;
  pid bigint;
  j jsonb;
  srun bigint;
  orun bigint;
  v_load_version bigint;
BEGIN
  src := pg_temp.source('I0605_TEST_PG');
  o1 := pg_temp.make_object(src, 'requests', true);
  o2 := pg_temp.make_object(src, 'requests2', true);
  CREATE TABLE stg.i0605_test_t (id text PRIMARY KEY, pin text, name text, birth text, channel text);
  CREATE TABLE ods.i0605_test_t (id text PRIMARY KEY, pin text, name text, birth date, channel text, source_code text,
    loaded_at timestamptz, load_run_id bigint);

  -- ODS draft before any STG configuration: saved, but cannot be published.
  orev := app.integration_ods_config_save_draft(tok, o1, NULL, pg_temp.odoc('i0605_test_t', pg_temp.good(), '["id"]'));
  PERFORM app.integration_ods_config_record_validation(tok, o1, orev, true, '[]');
  PERFORM pg_temp.expect_fail('T01 publish without a published STG configuration',
    format('SELECT app.integration_ods_config_publish(%L, %s, %s)', tok, o1, orev));
  PERFORM pg_temp.expect_fail('T02 transformation outside the white list rejected',
    pg_temp.save(o1, orev, pg_temp.odoc('i0605_test_t', jsonb_build_array(pg_temp.o('id', 'SQL', 'id', 'TEXT')), '["id"]')));
  PERFORM pg_temp.expect_fail('T03 DATE_FROM_TEXT with a format outside the list rejected',
    pg_temp.save(o1, orev, pg_temp.odoc('i0605_test_t',
      jsonb_build_array(pg_temp.o('birth', 'DATE_FROM_TEXT', 'birth', 'DATE', 'MM/DD/YY')), '[]')));
  PERFORM pg_temp.expect_fail('T04 technical field LOADED_AT must be INSERT_ONLY',
    pg_temp.save(o1, orev, pg_temp.odoc('i0605_test_t',
      jsonb_build_array(pg_temp.o('loaded_at', 'LOADED_AT', NULL, 'TIMESTAMPTZ')), '[]')));
  PERFORM pg_temp.expect_fail('T05 CONSTANT with an STG column rejected',
    pg_temp.save(o1, orev, pg_temp.odoc('i0605_test_t',
      jsonb_build_array(pg_temp.o('source_code', 'CONSTANT', 'pin', 'TEXT', 'OC')), '[]')));
  PERFORM pg_temp.expect_fail('T06 CONSTANT without a value rejected',
    pg_temp.save(o1, orev, pg_temp.odoc('i0605_test_t',
      jsonb_build_array(pg_temp.o('source_code', 'CONSTANT', NULL, 'TEXT')), '[]')));

  -- Published STG (LOAD) configuration of object 1 (functions of 008/011).
  rev := app.integration_object_config_save_draft(tok, o1, NULL, jsonb_build_object('target_schema', 'stg',
    'target_table', 'i0605_test_t', 'fields', jsonb_build_array(pg_temp.f('id'), pg_temp.f('pin'), pg_temp.f('name'),
    pg_temp.f('birth'), pg_temp.f('channel')), 'key', '["id"]'::jsonb));
  PERFORM app.integration_object_config_record_validation(tok, o1, rev, true, '[]');
  PERFORM app.integration_object_config_publish(tok, o1, rev);
  SELECT version_id INTO v_load_version FROM meta.object_config_current WHERE source_object_id = o1;

  orev := app.integration_ods_config_save_draft(tok, o1, orev, pg_temp.odoc('i0605_test_t', pg_temp.good(), '["id"]'));
  PERFORM pg_temp.expect_fail('T07 publish without a validation of this revision',
    format('SELECT app.integration_ods_config_publish(%L, %s, %s)', tok, o1, orev));
  orev := app.integration_ods_config_save_draft(tok, o1, orev, pg_temp.odoc('i0605_new', pg_temp.good(), '["id"]'));
  PERFORM app.integration_ods_config_record_validation(tok, o1, orev, true, '[]');
  PERFORM pg_temp.expect_fail('T08 publish into a missing ODS table',
    format('SELECT app.integration_ods_config_publish(%L, %s, %s)', tok, o1, orev));
  pid := app.integration_ods_proposal_create(tok, o1, 'CREATE_TABLE', 'i0605_new', 'CREATE TABLE ods.i0605_new (id text)');
  PERFORM pg_temp.ok('T09 ODS proposal stored for the ODS draft (schema ods, no LOAD version) and listed',
    (SELECT version_id IS NULL AND ods_version_id IS NOT NULL AND target_schema = 'ods' AND status = 'PROPOSED'
       FROM meta.target_change_proposal WHERE proposal_id = pid)
    AND jsonb_array_length(app.integration_object_config_get(tok, o1) -> 'proposals') = 1);
  PERFORM pg_temp.expect_fail('T10 a proposal bound to both a LOAD and an ODS version rejected',
    format('UPDATE meta.target_change_proposal SET version_id = %s WHERE proposal_id = %s', v_load_version, pid));

  orev := app.integration_ods_config_save_draft(tok, o1, orev, pg_temp.odoc('i0605_test_t',
    pg_temp.good() || jsonb_build_array(pg_temp.o('extra', 'COPY', 'ghost', 'TEXT')), '["id"]'));
  PERFORM app.integration_ods_config_record_validation(tok, o1, orev, true, '[]');
  PERFORM pg_temp.expect_fail('T11 publish with an ODS or STG column that does not exist',
    format('SELECT app.integration_ods_config_publish(%L, %s, %s)', tok, o1, orev));
  orev := app.integration_ods_config_save_draft(tok, o1, orev, pg_temp.odoc('i0605_test_t', pg_temp.good(), '["source_code"]'));
  PERFORM app.integration_ods_config_record_validation(tok, o1, orev, true, '[]');
  PERFORM pg_temp.expect_fail('T12 key from a CONSTANT field rejected',
    format('SELECT app.integration_ods_config_publish(%L, %s, %s)', tok, o1, orev));
  orev := app.integration_ods_config_save_draft(tok, o1, orev, pg_temp.odoc('i0605_test_t', pg_temp.good(), '["id"]'));
  SELECT ods_version_id INTO ovid FROM meta.object_ods_version WHERE source_object_id = o1 AND status = 'DRAFT';
  PERFORM pg_temp.ok('T13 definition holds transformations and values; hash of 64 hex characters',
    meta.object_ods_definition(ovid) #>> '{fields,3,param}' = 'YYYY-MM-DD'
    AND meta.object_ods_definition(ovid) #>> '{fields,5,transform}' = 'CONSTANT'
    AND meta.object_ods_definition(ovid) ->> 'target_schema' = 'ods'
    AND (SELECT definition_hash ~ '^[0-9a-f]{64}$' FROM meta.object_ods_version WHERE ods_version_id = ovid));
  PERFORM app.integration_ods_config_record_validation(tok, o1, orev, true, '[]');
  PERFORM app.integration_ods_config_publish(tok, o1, orev);
  j := app.integration_ods_config_get(tok, o1);
  PERFORM pg_temp.ok('T14 ODS version 1 published, READY; get shows STG table and no draft',
    j #>> '{current,status}' = 'READY' AND (j #>> '{current,version_no}')::int = 1
    AND j #>> '{stg,table}' = 'i0605_test_t' AND j -> 'draft' = 'null'::jsonb);
  PERFORM pg_temp.expect_fail('T15 published ODS version is immutable',
    format('UPDATE meta.object_ods_field SET param = %L WHERE ods_version_id = %s AND target_column = %L', 'X', ovid, 'channel'));

  -- Object 2: its own STG table, the same ODS table, AUTHORITATIVE on the same column -> refused.
  CREATE TABLE stg.i0605_test_t2 (id text PRIMARY KEY, pin text);
  rev := app.integration_object_config_save_draft(tok, o2, NULL, jsonb_build_object('target_schema', 'stg',
    'target_table', 'i0605_test_t2', 'fields', jsonb_build_array(pg_temp.f('id'), pg_temp.f('pin')),
    'key', '["id"]'::jsonb));
  PERFORM app.integration_object_config_record_validation(tok, o2, rev, true, '[]');
  PERFORM app.integration_object_config_publish(tok, o2, rev);
  orev := app.integration_ods_config_save_draft(tok, o2, NULL,
    pg_temp.odoc('i0605_test_t', jsonb_build_array(pg_temp.o('id', 'COPY', 'id', 'TEXT'), pg_temp.o('pin', 'UPPER', 'pin', 'TEXT')), '["id"]'));
  PERFORM app.integration_ods_config_record_validation(tok, o2, orev, true, '[]');
  PERFORM pg_temp.expect_fail('T16 two objects AUTHORITATIVE for the same ODS column',
    format('SELECT app.integration_ods_config_publish(%L, %s, %s)', tok, o2, orev));

  -- A new LOAD draft of object 1 does not touch the ODS version in force (O1).
  rev := app.integration_object_config_save_draft(tok, o1, NULL, jsonb_build_object('target_schema', 'stg',
    'target_table', 'i0605_test_t', 'fields', jsonb_build_array(pg_temp.f('id'), pg_temp.f('pin')), 'key', '["id"]'::jsonb));
  PERFORM pg_temp.ok('T17 LOAD draft leaves the ODS version in force unchanged',
    (SELECT ods_version_id = ovid AND status = 'READY' FROM meta.object_ods_current WHERE source_object_id = o1));

  -- ODS step of a run.
  j := etl.object_run_begin(o1);
  srun := (j ->> 'run_id')::bigint;
  PERFORM pg_temp.expect_fail('T18 ODS run with an STG run that has not succeeded',
    format('SELECT etl.object_ods_run_begin(%s, %s)', o1, srun));
  PERFORM etl.object_run_finish(srun, true, 0, 0, 0, 0, NULL, NULL, NULL, NULL);
  j := etl.object_ods_run_begin(o1, srun);
  orun := (j ->> 'ods_run_id')::bigint;
  PERFORM pg_temp.ok('T19 ODS run begins: own batch (OBJECT_LOAD) and job OBJECT_ODS_<id>, STG table, definition',
    j #>> '{stg,table}' = 'i0605_test_t' AND j #>> '{definition,target_table}' = 'i0605_test_t'
    AND (SELECT b.pipeline_code = 'OBJECT_LOAD' AND b.batch_code = 'OBJECT_ODS_' || o1 AND b.status = 'RUNNING'
           FROM etl.batch_run b WHERE b.batch_run_id = (j ->> 'batch_run_id')::bigint)
    AND (SELECT jb.code = 'OBJECT_ODS_' || o1 AND jb.job_type = 'TRANSFORM' FROM etl.job_run jr JOIN etl.job jb USING (job_id)
          WHERE jr.job_run_id = (j ->> 'job_run_id')::bigint)
    AND (SELECT stg_run_id = srun FROM etl.object_ods_run WHERE ods_run_id = orun));
  INSERT INTO etl.object_ods_record_change(ods_run_id, change_kind, record_key, changes)
  VALUES (orun, 'INSERT', '{"id": "1"}', '{"pin": {"before": null, "after": "P1"}}');
  PERFORM etl.object_ods_run_finish(orun, true, 1, 1, 0, 0, NULL, NULL, NULL, NULL);
  PERFORM pg_temp.ok('T20 ODS run SUCCESS: counts in ODS run, job_run and batch_run',
    (SELECT status = 'SUCCESS' AND rows_inserted = 1 FROM etl.object_ods_run WHERE ods_run_id = orun)
    AND (SELECT jr.status = 'SUCCESS' AND jr.rows_inserted = 1 AND b.status = 'SUCCESS'
           FROM etl.object_ods_run r JOIN etl.job_run jr USING (job_run_id) JOIN etl.batch_run b ON b.batch_run_id = r.batch_run_id
          WHERE r.ods_run_id = orun));
  PERFORM pg_temp.expect_fail('T21 ODS history after the run finished, or changed, rejected',
    format('INSERT INTO etl.object_ods_record_change(ods_run_id, change_kind, record_key, changes) VALUES (%s, %L, %L, %L)',
           orun, 'INSERT', '{"id": "2"}', '{}'));
  PERFORM pg_temp.expect_fail('T21b ODS history row cannot be updated',
    format('UPDATE etl.object_ods_record_change SET changes = %L WHERE ods_run_id = %s', '{}', orun));
  j := etl.object_ods_run_begin(o1, NULL);
  PERFORM etl.object_ods_run_finish((j ->> 'ods_run_id')::bigint, false, 0, 0, 0, 0, 'TYPE_CONVERSION',
    'birth: value is not a date', '{"id": "3"}', 'birth');
  PERFORM pg_temp.ok('T22 failed ODS run (started alone): FAILED, error_log stage ODS_LOAD, batch FAILED, visible in the run list',
    (SELECT status = 'FAILED' AND error_code = 'TYPE_CONVERSION' AND stg_run_id IS NULL
       FROM etl.object_ods_run WHERE ods_run_id = (j ->> 'ods_run_id')::bigint)
    AND EXISTS (SELECT 1 FROM etl.error_log e WHERE e.batch_run_id = (j ->> 'batch_run_id')::bigint
                  AND e.error_stage = 'ODS_LOAD' AND e.column_name = 'birth')
    AND EXISTS (SELECT 1 FROM etl.v_integration_run_history h
                 WHERE h.batch_run_id = (j ->> 'batch_run_id')::bigint AND h.status = 'FAILED'));
  PERFORM pg_advisory_unlock_all();
END
$t$;

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
