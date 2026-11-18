-- I23 test of 023: SQL versions of DM marts -- saving the query, checks of the version, publication through
-- the mechanism of 019 (build aside and swap), new drafts copying the query, back to the constructor, refresh after
-- ODS of the tables of the query, the role dwh_dm_sql_ro (reads ods, nothing else, cannot write).
-- One transaction, ROLLED BACK; session advisory locks released at the end. For E0 and E1, never the working dwh.
-- Test objects ods.i23_* / dm.i23_* exist only inside it. The session function is replaced inside the transaction
-- (as in 018/019). Run as the schema owner: psql -v ON_ERROR_STOP=1 -f 023_sql_mart_test.sql
-- Expected last line: ALL_TESTS_PASS (labels Q01-Q14: 14 checks).
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
    PERFORM set_config('dwh.actor_user_id', '9201', true);
    PERFORM set_config('dwh.actor_username', 'i23_admin', true);
    RETURN 9201;
  END IF;
  RAISE EXCEPTION 'Integration administration access denied';
END;
$function$;

-- What the engine does for one request (the SQL of the version, read here directly; the real engine reads it
-- as dwh_dm_sql_ro): claim, begin, fill the target table, finish.
CREATE FUNCTION pg_temp.engine(p_worker text DEFAULT 'i23-worker') RETURNS text
LANGUAGE plpgsql AS $fn$
DECLARE
  c record;
  v_run bigint;
  v_code text;
  v_target text;
  v_sql text;
  v_rows bigint;
BEGIN
  SELECT * INTO c FROM etl.dm_mart_run_request_claim(p_worker);
  IF c.request_id IS NULL THEN RETURN 'EMPTY'; END IF;
  v_run := etl.dm_mart_run_begin(c.request_id, p_worker, c.version_id);
  SELECT m.mart_code INTO v_code FROM meta.dm_mart m WHERE m.mart_id = c.mart_id;
  v_target := CASE WHEN c.trigger_kind = 'PUBLISH' THEN v_code || '__next' ELSE v_code END;
  SELECT v.sql_text INTO v_sql FROM meta.dm_mart_version v
   WHERE v.version_id = coalesce(c.version_id, (SELECT x.version_id FROM meta.dm_mart_version x
                                                  WHERE x.mart_id = c.mart_id AND x.status = 'PUBLISHED'));
  EXECUTE format('DELETE FROM dm.%I', v_target);
  EXECUTE format('INSERT INTO dm.%I (client_id, name, total, dm_loaded_at) SELECT client_id, name, total, now() FROM (%s) q',
                 v_target, v_sql);
  GET DIAGNOSTICS v_rows = ROW_COUNT;
  RETURN c.trigger_kind || ':' || etl.dm_mart_run_finish(v_run, true, 0, v_rows, NULL, NULL, NULL);
END
$fn$;

CREATE TABLE ods.i23_client (client_id bigint, name text);
CREATE TABLE ods.i23_contract (client_id bigint, amount numeric);
CREATE TABLE ods.i23_other (x int);
INSERT INTO ods.i23_client VALUES (1, 'A'), (2, 'B'), (3, 'C');
INSERT INTO ods.i23_contract VALUES (1, 10), (1, 5), (2, 7);

CREATE TEMP TABLE t_id (k text PRIMARY KEY, v bigint);
INSERT INTO t_id SELECT 'mart', app.dm_mart_save(repeat('a', 64), NULL, 'i23_mart', 'Clients by SQL (test)', NULL,
  'REPORT_VIEW', true, true, true);
INSERT INTO t_id SELECT 'v1', app.dm_mart_version_create(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'mart'));

CREATE TEMP TABLE t_sql (k text PRIMARY KEY, s text);
INSERT INTO t_sql VALUES ('q1', 'SELECT c.client_id, c.name, coalesce(sum(k.amount), 0) AS total FROM ods.i23_client c '
  || 'LEFT JOIN ods.i23_contract k ON k.client_id = c.client_id GROUP BY c.client_id, c.name '
  || 'UNION ALL SELECT 100, ''union row'', 0');
CREATE FUNCTION pg_temp.fields(p_key boolean DEFAULT true) RETURNS jsonb LANGUAGE sql AS $fn$
  SELECT jsonb_build_array(jsonb_build_object('name', 'client_id', 'type', 'bigint', 'is_key', p_key),
                           jsonb_build_object('name', 'name', 'title', 'Name', 'type', 'text'),
                           jsonb_build_object('name', 'total', 'type', 'numeric'))
$fn$;

CREATE TEMP TABLE t_res (k text PRIMARY KEY, r jsonb);
INSERT INTO t_res SELECT 'save1', to_jsonb(app.sql_mart_save(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v1'),
  '  ' || (SELECT s FROM t_sql WHERE k = 'q1') || '  ', ARRAY['i23_contract', 'i23_client', 'i23_client'], pg_temp.fields()));
SELECT pg_temp.ok('Q01 SQL version saved: kind SQL, trimmed text with its SHA-256, tables sorted and unique, fields, no problems',
  (SELECT r FROM t_res WHERE k = 'save1') = '[]'::jsonb
  AND (SELECT version_kind = 'SQL' AND sql_text = (SELECT s FROM t_sql WHERE k = 'q1')
              AND sql_sha256 = encode(sha256(convert_to(sql_text, 'UTF8')), 'hex')
              AND sql_tables = ARRAY['i23_client', 'i23_contract'] AND grain_entity_id IS NULL
         FROM meta.dm_mart_version WHERE version_id = (SELECT v FROM t_id WHERE k = 'v1'))
  AND (SELECT count(*) FROM meta.dm_mart_field WHERE version_id = (SELECT v FROM t_id WHERE k = 'v1')) = 3);

SELECT pg_temp.expect_fail('Q02a empty SQL text',
  format('SELECT app.sql_mart_save(%L, %s, %L, ARRAY[''i23_client''], %L::jsonb)', repeat('a', 64),
         (SELECT v FROM t_id WHERE k = 'v1'), '   ', pg_temp.fields()::text), '1-50000');
SELECT pg_temp.expect_fail('Q02b invalid table name',
  format('SELECT app.sql_mart_save(%L, %s, %L, ARRAY[''meta.dm_mart''], %L::jsonb)', repeat('a', 64),
         (SELECT v FROM t_id WHERE k = 'v1'), 'SELECT 1', pg_temp.fields()::text), 'tables of schema ods');
SELECT pg_temp.expect_fail('Q02c no tables',
  format('SELECT app.sql_mart_save(%L, %s, %L, ARRAY[]::text[], %L::jsonb)', repeat('a', 64),
         (SELECT v FROM t_id WHERE k = 'v1'), 'SELECT 1', pg_temp.fields()::text), 'tables of schema ods');
SELECT pg_temp.expect_fail('Q02d fields are not an array',
  format('SELECT app.sql_mart_save(%L, %s, %L, ARRAY[''i23_client''], %L::jsonb)', repeat('a', 64),
         (SELECT v FROM t_id WHERE k = 'v1'), 'SELECT 1', '{}'), 'Invalid fields');
SELECT pg_temp.expect_fail('Q02e another session',
  format('SELECT app.sql_mart_save(%L, %s, %L, ARRAY[''i23_client''], %L::jsonb)', repeat('b', 64),
         (SELECT v FROM t_id WHERE k = 'v1'), 'SELECT 1', pg_temp.fields()::text), 'access denied');
SELECT pg_temp.ok('Q02 invalid input of the SQL version is rejected', true);

-- Q03: problems -- a table that does not exist in ods, no key field.
INSERT INTO t_id SELECT 'mart_p', app.dm_mart_save(repeat('a', 64), NULL, 'i23_mart_p', 'Problems (test)', NULL,
  'REPORT_VIEW', false, false, true);
INSERT INTO t_id SELECT 'vp', app.dm_mart_version_create(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'mart_p'));
INSERT INTO t_res SELECT 'save_p', to_jsonb(app.sql_mart_save(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'vp'),
  'SELECT 1 AS client_id, ''x'' AS name, 0 AS total', ARRAY['i23_missing'], pg_temp.fields(false)));
SELECT pg_temp.ok('Q03 problems: missing ods table, no key field',
  (SELECT r FROM t_res WHERE k = 'save_p') ? 'table ods.i23_missing not found'
  AND (SELECT r FROM t_res WHERE k = 'save_p') ? 'exactly one key field is required');

SELECT pg_temp.expect_fail('Q04 constraint: a SQL version needs its text',
  format('UPDATE meta.dm_mart_version SET sql_text = NULL WHERE version_id = %s', (SELECT v FROM t_id WHERE k = 'v1')),
  'dm_mart_version_sql_shape');

INSERT INTO t_res SELECT 'pub1', app.mart_builder_publish(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v1'), false);
SELECT pg_temp.ok('Q05 publication of a SQL version builds aside: dm.i23_mart__next with the fields, PUBLISH request',
  (SELECT r->>'status' FROM t_res WHERE k = 'pub1') = 'BUILDING'
  AND to_regclass('dm.i23_mart__next') IS NOT NULL
  AND (SELECT string_agg(attname, ',' ORDER BY attnum) FROM pg_attribute
        WHERE attrelid = 'dm.i23_mart__next'::regclass AND attnum > 0 AND NOT attisdropped) = 'client_id,name,total,dm_loaded_at'
  AND EXISTS (SELECT 1 FROM etl.dm_mart_run_request WHERE trigger_kind = 'PUBLISH' AND status = 'QUEUED'
               AND version_id = (SELECT v FROM t_id WHERE k = 'v1')));
INSERT INTO t_res SELECT 'run1', to_jsonb(pg_temp.engine());
SELECT pg_temp.ok('Q06 build and swap: dm.i23_mart with the rows of the query (UNION included), v1 PUBLISHED',
  (SELECT r #>> '{}' FROM t_res WHERE k = 'run1') = 'PUBLISH:SUCCESS'
  AND (SELECT count(*) FROM dm.i23_mart) = 4
  AND (SELECT total FROM dm.i23_mart WHERE client_id = 1) = 15
  AND (SELECT status FROM meta.dm_mart_version WHERE version_id = (SELECT v FROM t_id WHERE k = 'v1')) = 'PUBLISHED');

INSERT INTO t_res SELECT 'enq', jsonb_build_array(etl.dm_mart_enqueue_after_ods('i23_other'),
                                                 etl.dm_mart_enqueue_after_ods('i23_contract'));
SELECT pg_temp.ok('Q07 refresh after ODS: a table of the query queues the SQL mart, another table does not',
  (SELECT r FROM t_res WHERE k = 'enq') = '[0, 1]'::jsonb
  AND EXISTS (SELECT 1 FROM etl.dm_mart_run_request r JOIN meta.dm_mart m ON m.mart_id = r.mart_id
               WHERE m.mart_code = 'i23_mart' AND r.trigger_kind = 'AFTER_ODS' AND r.trigger_table = 'i23_contract'
                 AND r.status = 'QUEUED'));
INSERT INTO t_res SELECT 'run2', to_jsonb(pg_temp.engine());
SELECT pg_temp.ok('Q08 refresh of the published SQL mart runs its query',
  (SELECT r #>> '{}' FROM t_res WHERE k = 'run2') = 'AFTER_ODS:SUCCESS' AND (SELECT count(*) FROM dm.i23_mart) = 4);

INSERT INTO t_id SELECT 'v2', app.dm_mart_version_create(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'mart'));
SELECT pg_temp.ok('Q09 a new draft of a SQL mart copies the query, its SHA-256, tables and fields',
  (SELECT version_kind = 'SQL' AND sql_text = (SELECT s FROM t_sql WHERE k = 'q1') AND sql_tables = ARRAY['i23_client', 'i23_contract']
     FROM meta.dm_mart_version WHERE version_id = (SELECT v FROM t_id WHERE k = 'v2'))
  AND (SELECT count(*) FROM meta.dm_mart_field WHERE version_id = (SELECT v FROM t_id WHERE k = 'v2')) = 3
  AND meta.dm_mart_version_problems((SELECT v FROM t_id WHERE k = 'v2')) = ARRAY[]::text[]);

SELECT app.dm_mart_version_save(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v2'), '{"sources": [], "fields": []}'::jsonb);
SELECT pg_temp.ok('Q10 a constructor save makes the draft a constructor version again (no query), constructor checks apply',
  (SELECT version_kind = 'BUILDER' AND sql_text IS NULL AND sql_tables IS NULL AND sql_sha256 IS NULL
     FROM meta.dm_mart_version WHERE version_id = (SELECT v FROM t_id WHERE k = 'v2'))
  AND 'GRAIN source is missing' = ANY (meta.dm_mart_version_problems((SELECT v FROM t_id WHERE k = 'v2'))));

INSERT INTO meta.dm_mart_source (version_id, position, alias, table_name, source_kind, key_column)
VALUES ((SELECT v FROM t_id WHERE k = 'vp'), 1, 'c', 'i23_client', 'GRAIN', 'client_id');
SELECT pg_temp.ok('Q11 a SQL version with constructor parts is not valid',
  'a SQL version has no sources, field sources or filters' = ANY (meta.dm_mart_version_problems((SELECT v FROM t_id WHERE k = 'vp'))));

SELECT pg_temp.ok('Q12 dwh_app may call app.sql_mart_save; meta.sql_mart_problems is the owner''s only',
  has_function_privilege('dwh_app', 'app.sql_mart_save(text, bigint, text, text[], jsonb)', 'EXECUTE')
  AND NOT has_function_privilege('dwh_app', 'meta.sql_mart_problems(bigint)', 'EXECUTE')
  AND NOT has_function_privilege('dwh_dm_sql_ro', 'app.sql_mart_save(text, bigint, text, text[], jsonb)', 'EXECUTE'));

-- dwh_dm_sql_ro: reads ods, nothing else, cannot write (plain DO block: the role does not need the test functions).
SET LOCAL ROLE dwh_dm_sql_ro;
SET LOCAL transaction_read_only = on;
DO $role$
DECLARE
  t record;
  n bigint;
BEGIN
  SELECT count(*) INTO n FROM ods.i23_client;
  IF n <> 3 THEN RAISE EXCEPTION 'FAIL Q13 dwh_dm_sql_ro reads ods.i23_client'; END IF;
  RAISE NOTICE 'PASS Q13 dwh_dm_sql_ro reads ods (a table created after 023: default privileges)';
  FOR t IN SELECT * FROM (VALUES
    ('Q14a cannot read meta', 'SELECT count(*) FROM meta.dm_mart'),
    ('Q14b cannot read app', 'SELECT count(*) FROM app.user_account'),
    ('Q14c cannot read dm', 'SELECT count(*) FROM dm.i23_mart'),
    ('Q14d cannot write to ods', 'DELETE FROM ods.i23_client WHERE false'),
    ('Q14e cannot create in ods', 'CREATE TABLE ods.i23_x (a int)')) AS x(label, stmt)
  LOOP
    BEGIN
      EXECUTE t.stmt;
      RAISE EXCEPTION 'FAIL %: statement was accepted', t.label;
    EXCEPTION WHEN insufficient_privilege OR read_only_sql_transaction THEN
      RAISE NOTICE 'PASS %: rejected (%)', t.label, SQLERRM;
    END;
  END LOOP;
  RAISE NOTICE 'PASS Q14 dwh_dm_sql_ro reads nothing else and cannot write';
END
$role$;
RESET ROLE;

SELECT pg_advisory_unlock_all();
SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
