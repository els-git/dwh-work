-- I19 test of 018: DM mart builder -- catalog of keys, marts and versions (grain, same key, child with
-- aggregates, lookups and roles of one table, filters), problems of a version, DDL proposal, publication with
-- the AI whitelist, run requests (manual, after ODS, claim/begin/finish, lost dispatcher, cancel).
-- One transaction, ROLLED BACK; session advisory locks released at the end. For E0 (sandbox with fixtures)
-- and E1 (restored copy), never the working dwh. Test tables ods.i19t_* and dm.i19t_* exist only inside it.
-- app.integration_admin_session_user() and app.integration_run_admin_session_user() are replaced inside the
-- transaction (as in 008/010-015). Run as the schema owner: psql -v ON_ERROR_STOP=1 -f 018_dm_builder_test.sql
-- Expected last line: ALL_TESTS_PASS (labels D01-D31: 31 checks).
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

-- Sessions: token a... = administrator 9001, token b... = a user without the right.
CREATE OR REPLACE FUNCTION app.integration_admin_session_user(p_token_hash text)
 RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog' AS $function$
BEGIN
  IF p_token_hash = repeat('a', 64) THEN
    PERFORM set_config('dwh.actor_user_id', '9001', true);
    PERFORM set_config('dwh.actor_username', 'i19_admin', true);
    RETURN 9001;
  END IF;
  RAISE EXCEPTION 'Integration administration access denied';
END;
$function$;
CREATE OR REPLACE FUNCTION app.integration_run_admin_session_user(p_token_hash text)
 RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog' AS $function$
BEGIN
  IF p_token_hash = repeat('a', 64) THEN
    PERFORM set_config('dwh.actor_user_id', '9001', true);
    PERFORM set_config('dwh.actor_username', 'i19_admin', true);
    RETURN 9001;
  END IF;
  RAISE EXCEPTION 'Integration run access denied';
END;
$function$;

-- Synthetic ODS: clients (grain), client phones (same key, other name), accounts (child, key cust_no),
-- contracts (text key: incompatible), statuses (lookup), managers (lookup in two roles).
CREATE TABLE ods.i19t_client (client_id bigint, full_name text, status_code text, manager_id bigint, backup_manager_id bigint);
CREATE TABLE ods.i19t_client_phone (cl_id integer, phone text);
CREATE TABLE ods.i19t_account (cust_no bigint, amount numeric, state text);
CREATE TABLE ods.i19t_contract (customer_ref text, contract_no text);
CREATE TABLE ods.i19t_status (code text, name_ru text);
CREATE TABLE ods.i19t_manager (id bigint, fio text);
CREATE TABLE dm.i19t_exists (x integer);

CREATE TEMP TABLE t_id (k text PRIMARY KEY, v bigint);
INSERT INTO t_id SELECT 'entity', app.dm_entity_save(repeat('a', 64), NULL, 'i19t_client', 'Client (test)', true);
SELECT pg_temp.ok('D01 entity saved, code upper-cased',
  (SELECT entity_code FROM meta.dm_entity WHERE entity_id = (SELECT v FROM t_id WHERE k = 'entity')) = 'I19T_CLIENT');

SELECT app.dm_entity_key_save(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'entity'), 'i19t_client', 'client_id', true);
SELECT app.dm_entity_key_save(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'entity'), 'i19t_client_phone', 'cl_id', true);
SELECT app.dm_entity_key_save(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'entity'), 'i19t_account', 'cust_no', true);
SELECT pg_temp.ok('D02 keys with different names, bigint and integer are one family',
  (SELECT count(*) FROM meta.dm_entity_key WHERE entity_id = (SELECT v FROM t_id WHERE k = 'entity')
     AND type_family = 'NUMBER') = 3);
SELECT pg_temp.expect_fail('D03 text key of a numeric entity',
  format('SELECT app.dm_entity_key_save(%L, %s, %L, %L, true)', repeat('a', 64), (SELECT v FROM t_id WHERE k = 'entity'),
         'i19t_contract', 'customer_ref'), 'not compatible');
SELECT pg_temp.expect_fail('D04 key on a missing column',
  format('SELECT app.dm_entity_key_save(%L, %s, %L, %L, true)', repeat('a', 64), (SELECT v FROM t_id WHERE k = 'entity'),
         'i19t_client', 'no_such'), 'not found');

INSERT INTO t_id SELECT 'mart', app.dm_mart_save(repeat('a', 64), NULL, 'i19t_client_mart', 'Clients (test)',
  'One row per client', 'REPORT_VIEW', true, true, true);
SELECT pg_temp.ok('D05 mart saved, no AI row before publication',
  (SELECT mart_code FROM meta.dm_mart WHERE mart_id = (SELECT v FROM t_id WHERE k = 'mart')) = 'i19t_client_mart'
  AND NOT EXISTS (SELECT 1 FROM meta.ai_report_object WHERE object_name = 'i19t_client_mart'));
SELECT pg_temp.expect_fail('D06 code of an existing dm object',
  format('SELECT app.dm_mart_save(%L, NULL, %L, %L, NULL, NULL, false, true, true)', repeat('a', 64), 'i19t_exists', 'x'),
  'already exists');
SELECT pg_temp.expect_fail('D07 unknown permission',
  format('SELECT app.dm_mart_save(%L, NULL, %L, %L, NULL, %L, false, true, true)', repeat('a', 64), 'i19t_other', 'x',
         'NO_SUCH_RIGHT'), 'not found');

INSERT INTO t_id SELECT 'v1', app.dm_mart_version_create(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'mart'));
SELECT pg_temp.expect_fail('D08 second draft',
  format('SELECT app.dm_mart_version_create(%L, %s)', repeat('a', 64), (SELECT v FROM t_id WHERE k = 'mart')), 'draft');

CREATE TEMP TABLE t_def (k text PRIMARY KEY, d jsonb);
INSERT INTO t_def VALUES ('good', jsonb_build_object(
  'grain_entity_id', (SELECT v FROM t_id WHERE k = 'entity'),
  'sources', jsonb_build_array(
    jsonb_build_object('alias', 'c', 'table', 'i19t_client', 'kind', 'GRAIN'),
    jsonb_build_object('alias', 'p', 'table', 'i19t_client_phone', 'kind', 'SAME_KEY'),
    jsonb_build_object('alias', 'a', 'table', 'i19t_account', 'kind', 'CHILD'),
    jsonb_build_object('alias', 'st', 'table', 'i19t_status', 'kind', 'LOOKUP', 'ref_alias', 'c', 'ref_column', 'status_code',
                       'lookup_column', 'code'),
    jsonb_build_object('alias', 'mgr', 'table', 'i19t_manager', 'kind', 'LOOKUP', 'ref_alias', 'c', 'ref_column', 'manager_id',
                       'lookup_column', 'id'),
    jsonb_build_object('alias', 'bmgr', 'table', 'i19t_manager', 'kind', 'LOOKUP', 'ref_alias', 'c',
                       'ref_column', 'backup_manager_id', 'lookup_column', 'id')),
  'fields', jsonb_build_array(
    jsonb_build_object('name', 'client_id', 'type', 'bigint', 'is_key', true,
                       'sources', jsonb_build_array(jsonb_build_object('alias', 'c', 'column', 'client_id'))),
    jsonb_build_object('name', 'full_name', 'type', 'varchar(200)', 'title', 'Name',
                       'sources', jsonb_build_array(jsonb_build_object('alias', 'c', 'column', 'full_name'))),
    jsonb_build_object('name', 'contact', 'type', 'text',
                       'sources', jsonb_build_array(jsonb_build_object('alias', 'p', 'column', 'phone'),
                                                    jsonb_build_object('alias', 'c', 'column', 'full_name'))),
    jsonb_build_object('name', 'status_name', 'type', 'text',
                       'sources', jsonb_build_array(jsonb_build_object('alias', 'st', 'column', 'name_ru'))),
    jsonb_build_object('name', 'manager_fio', 'type', 'text',
                       'sources', jsonb_build_array(jsonb_build_object('alias', 'mgr', 'column', 'fio'))),
    jsonb_build_object('name', 'backup_manager_fio', 'type', 'text',
                       'sources', jsonb_build_array(jsonb_build_object('alias', 'bmgr', 'column', 'fio'))),
    jsonb_build_object('name', 'balance', 'type', 'numeric(18,2)',
                       'sources', jsonb_build_array(jsonb_build_object('alias', 'a', 'column', 'amount', 'aggregate', 'sum'))),
    jsonb_build_object('name', 'accounts', 'type', 'bigint',
                       'sources', jsonb_build_array(jsonb_build_object('alias', 'a', 'column', 'cust_no', 'aggregate', 'COUNT'))),
    jsonb_build_object('name', 'last_state', 'type', 'text',
                       'sources', jsonb_build_array(jsonb_build_object('alias', 'a', 'column', 'state', 'aggregate', 'MAX')))),
  'filters', jsonb_build_array(
    jsonb_build_object('alias', 'a', 'column', 'state', 'operator', 'in', 'value', jsonb_build_array('OPEN', 'FROZEN')),
    jsonb_build_object('alias', 'c', 'column', 'status_code', 'operator', 'is not null'))));

CREATE TEMP TABLE t_res (k text PRIMARY KEY, r text[]);
INSERT INTO t_res SELECT 'good', app.dm_mart_version_save(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v1'),
  (SELECT d FROM t_def WHERE k = 'good'));
SELECT pg_temp.ok('D09 valid definition: no problems; 6 sources (manager table in two roles), 9 fields, 10 field sources, 2 filters',
  (SELECT cardinality(r) FROM t_res WHERE k = 'good') = 0
  AND (SELECT count(*) FROM meta.dm_mart_source WHERE version_id = (SELECT v FROM t_id WHERE k = 'v1')) = 6
  AND (SELECT count(*) FROM meta.dm_mart_source WHERE version_id = (SELECT v FROM t_id WHERE k = 'v1')
         AND table_name = 'i19t_manager') = 2
  AND (SELECT count(*) FROM meta.dm_mart_field WHERE version_id = (SELECT v FROM t_id WHERE k = 'v1')) = 9
  AND (SELECT count(*) FROM meta.dm_mart_field_source s JOIN meta.dm_mart_field f ON f.field_id = s.field_id
        WHERE f.version_id = (SELECT v FROM t_id WHERE k = 'v1')) = 10
  AND (SELECT count(*) FROM meta.dm_mart_filter WHERE version_id = (SELECT v FROM t_id WHERE k = 'v1')) = 2);
SELECT pg_temp.ok('D10 keys copied from the catalog, aggregate upper-cased, priority by order',
  (SELECT key_column FROM meta.dm_mart_source WHERE version_id = (SELECT v FROM t_id WHERE k = 'v1') AND alias = 'p') = 'cl_id'
  AND (SELECT key_column FROM meta.dm_mart_source WHERE version_id = (SELECT v FROM t_id WHERE k = 'v1') AND alias = 'a') = 'cust_no'
  AND EXISTS (SELECT 1 FROM meta.dm_mart_field_source s JOIN meta.dm_mart_field f ON f.field_id = s.field_id
               WHERE f.version_id = (SELECT v FROM t_id WHERE k = 'v1') AND f.field_name = 'balance' AND s.aggregate_fn = 'SUM')
  AND (SELECT string_agg(s.source_alias, ',' ORDER BY s.priority_no) FROM meta.dm_mart_field_source s
         JOIN meta.dm_mart_field f ON f.field_id = s.field_id
        WHERE f.version_id = (SELECT v FROM t_id WHERE k = 'v1') AND f.field_name = 'contact') = 'p,c');

-- Problems are reported, not raised: CHILD without aggregate, aggregate on GRAIN, lookup to a later source,
-- lookup to a CHILD source, missing column, key field not from GRAIN.
INSERT INTO t_res SELECT 'bad', app.dm_mart_version_save(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v1'), jsonb_build_object(
  'grain_entity_id', (SELECT v FROM t_id WHERE k = 'entity'),
  'sources', jsonb_build_array(
    jsonb_build_object('alias', 'c', 'table', 'i19t_client', 'kind', 'GRAIN'),
    jsonb_build_object('alias', 'st', 'table', 'i19t_status', 'kind', 'LOOKUP', 'ref_alias', 'mgr', 'ref_column', 'id',
                       'lookup_column', 'code'),
    jsonb_build_object('alias', 'a', 'table', 'i19t_account', 'kind', 'CHILD'),
    jsonb_build_object('alias', 'mgr', 'table', 'i19t_manager', 'kind', 'LOOKUP', 'ref_alias', 'a', 'ref_column', 'cust_no',
                       'lookup_column', 'id')),
  'fields', jsonb_build_array(
    jsonb_build_object('name', 'client_id', 'type', 'bigint', 'is_key', true,
                       'sources', jsonb_build_array(jsonb_build_object('alias', 'a', 'column', 'cust_no', 'aggregate', 'MAX'))),
    jsonb_build_object('name', 'amount', 'type', 'numeric',
                       'sources', jsonb_build_array(jsonb_build_object('alias', 'a', 'column', 'amount'))),
    jsonb_build_object('name', 'cnt', 'type', 'bigint',
                       'sources', jsonb_build_array(jsonb_build_object('alias', 'c', 'column', 'client_id', 'aggregate', 'COUNT'))),
    jsonb_build_object('name', 'ghost', 'type', 'text',
                       'sources', jsonb_build_array(jsonb_build_object('alias', 'c', 'column', 'no_such_column')))),
  'filters', jsonb_build_array(jsonb_build_object('alias', 'zz', 'column', 'x', 'operator', '=', 'value', 1))));
SELECT pg_temp.ok('D11 problems: earlier source, CHILD reference, aggregate rules, missing column, key field, filter source',
  (SELECT bool_and(EXISTS (SELECT 1 FROM unnest(r) u WHERE u LIKE e)) FROM t_res,
     unnest(ARRAY['%reference mgr must be an earlier source%', '%cannot refer to the CHILD source a%',
                  '%of a CHILD source needs an aggregate%', '%aggregate is allowed only for CHILD sources%',
                  '%no_such_column not found%', '%key field must be the key column of the GRAIN source%',
                  '%filter 1: unknown source zz%']) e
    WHERE k = 'bad'));
SELECT pg_temp.expect_fail('D12 IN filter with a scalar value',
  format('SELECT app.dm_mart_version_save(%L, %s, %L::jsonb)', repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v1'),
    jsonb_build_object('grain_entity_id', (SELECT v FROM t_id WHERE k = 'entity'),
      'sources', jsonb_build_array(jsonb_build_object('alias', 'c', 'table', 'i19t_client', 'kind', 'GRAIN')),
      'filters', jsonb_build_array(jsonb_build_object('alias', 'c', 'column', 'status_code', 'operator', 'IN', 'value', 'A')))::text),
  'dm_mart_filter_value');
SELECT pg_temp.expect_fail('D13 source table without a key of the grain entity',
  format('SELECT app.dm_mart_version_save(%L, %s, %L::jsonb)', repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v1'),
    jsonb_build_object('grain_entity_id', (SELECT v FROM t_id WHERE k = 'entity'),
      'sources', jsonb_build_array(jsonb_build_object('alias', 'k', 'table', 'i19t_contract', 'kind', 'GRAIN')))::text),
  'no active key');
SELECT pg_temp.expect_fail('D14 invalid field type',
  format('SELECT app.dm_mart_version_save(%L, %s, %L::jsonb)', repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v1'),
    jsonb_build_object('grain_entity_id', (SELECT v FROM t_id WHERE k = 'entity'),
      'sources', jsonb_build_array(jsonb_build_object('alias', 'c', 'table', 'i19t_client', 'kind', 'GRAIN')),
      'fields', jsonb_build_array(jsonb_build_object('name', 'x', 'type', 'text; drop table x',
        'sources', jsonb_build_array(jsonb_build_object('alias', 'c', 'column', 'full_name')))))::text),
  'dm_mart_field_data_type_check');

SELECT app.dm_mart_version_save(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v1'), (SELECT d FROM t_def WHERE k = 'good'));
SELECT pg_temp.expect_fail('D15 publish without the mart table',
  format('SELECT app.dm_mart_version_publish(%L, %s)', repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v1')),
  'does not match');

CREATE TEMP TABLE t_ddl AS SELECT * FROM app.dm_mart_ddl_propose(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v1'));
SELECT pg_temp.ok('D16 DDL proposed: CREATE TABLE with key, types and dm_loaded_at; SHA-256 of the text stored',
  (SELECT ddl_text FROM t_ddl) LIKE 'CREATE TABLE dm.i19t_client_mart (%'
  AND (SELECT ddl_text FROM t_ddl) LIKE '%client_id bigint NOT NULL,%full_name varchar(200),%balance numeric(18,2),%'
  AND (SELECT ddl_text FROM t_ddl) LIKE '%dm_loaded_at timestamptz NOT NULL,%PRIMARY KEY (client_id)%'
  AND (SELECT ddl_sha256 FROM t_ddl) = encode(sha256(convert_to((SELECT ddl_text FROM t_ddl), 'UTF8')), 'hex')
  AND (SELECT ddl_status FROM meta.dm_mart_version WHERE version_id = (SELECT v FROM t_id WHERE k = 'v1')) = 'PROPOSED');

-- As the applying package will do: the stored text after the SHA-256 check, then APPLIED.
DO $apply$
DECLARE v meta.dm_mart_version;
BEGIN
  SELECT * INTO v FROM meta.dm_mart_version WHERE version_id = (SELECT t.v FROM t_id t WHERE t.k = 'v1');
  IF encode(sha256(convert_to(v.ddl_text, 'UTF8')), 'hex') <> v.ddl_sha256 THEN RAISE EXCEPTION 'SHA mismatch'; END IF;
  EXECUTE v.ddl_text;
  UPDATE meta.dm_mart_version SET ddl_status = 'APPLIED' WHERE version_id = v.version_id;
END
$apply$;
SELECT pg_temp.ok('D17 applied table matches the fields of the version',
  meta.dm_mart_table_matches((SELECT v FROM t_id WHERE k = 'v1')));

SELECT app.dm_mart_version_publish(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v1'));
SELECT pg_temp.ok('D18 published; AI whitelist row active with the permission of the mart',
  (SELECT status FROM meta.dm_mart_version WHERE version_id = (SELECT v FROM t_id WHERE k = 'v1')) = 'PUBLISHED'
  AND EXISTS (SELECT 1 FROM meta.ai_report_object WHERE schema_name = 'dm' AND object_name = 'i19t_client_mart'
               AND is_active AND required_permission_code = 'REPORT_VIEW'));
SELECT pg_temp.ok('D19 default privileges of dm: SELECT for dwh_app, dwh_report, dwh_ai_ro; writes for dwh_etl',
  has_table_privilege('dwh_app', 'dm.i19t_client_mart', 'SELECT') AND has_table_privilege('dwh_ai_ro', 'dm.i19t_client_mart', 'SELECT')
  AND has_table_privilege('dwh_report', 'dm.i19t_client_mart', 'SELECT')
  AND has_table_privilege('dwh_etl', 'dm.i19t_client_mart', 'INSERT,DELETE,SELECT')
  AND NOT has_table_privilege('dwh_app', 'dm.i19t_client_mart', 'INSERT'));

INSERT INTO t_id SELECT 'v2', app.dm_mart_version_create(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'mart'));
SELECT pg_temp.ok('D20 new draft copies the published version',
  (SELECT version_no FROM meta.dm_mart_version WHERE version_id = (SELECT v FROM t_id WHERE k = 'v2')) = 2
  AND (SELECT count(*) FROM meta.dm_mart_source WHERE version_id = (SELECT v FROM t_id WHERE k = 'v2')) = 6
  AND (SELECT count(*) FROM meta.dm_mart_field_source s JOIN meta.dm_mart_field f ON f.field_id = s.field_id
        WHERE f.version_id = (SELECT v FROM t_id WHERE k = 'v2')) = 10
  AND (SELECT count(*) FROM meta.dm_mart_filter WHERE version_id = (SELECT v FROM t_id WHERE k = 'v2')) = 2);
SELECT pg_temp.expect_fail('D21 no DDL for an unchanged structure',
  format('SELECT * FROM app.dm_mart_ddl_propose(%L, %s)', repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v2')),
  'already has this structure');
SELECT app.dm_mart_version_publish(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v2'));
SELECT pg_temp.ok('D22 same structure published directly; v1 SUPERSEDED',
  (SELECT status FROM meta.dm_mart_version WHERE version_id = (SELECT v FROM t_id WHERE k = 'v1')) = 'SUPERSEDED'
  AND (SELECT status FROM meta.dm_mart_version WHERE version_id = (SELECT v FROM t_id WHERE k = 'v2')) = 'PUBLISHED');
SELECT pg_temp.expect_fail('D23 published version cannot be changed',
  format('SELECT app.dm_mart_version_save(%L, %s, %L::jsonb)', repeat('a', 64), (SELECT v FROM t_id WHERE k = 'v2'), '{}'),
  'Only a DRAFT');

-- Runs.
INSERT INTO t_id SELECT 'rq1', app.dm_mart_run_request_create(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'mart'));
SELECT pg_temp.expect_fail('D24 second active request',
  format('SELECT app.dm_mart_run_request_create(%L, %s)', repeat('a', 64), (SELECT v FROM t_id WHERE k = 'mart')),
  'already has an active');
SELECT pg_temp.ok('D25 after ODS: nothing while a request is queued; other tables do not queue',
  etl.dm_mart_enqueue_after_ods('i19t_account') = 0 AND etl.dm_mart_enqueue_after_ods('i19t_contract') = 0);
CREATE TEMP TABLE t_claim AS SELECT * FROM etl.dm_mart_run_request_claim('i19-worker');
INSERT INTO t_id SELECT 'run1', etl.dm_mart_run_begin((SELECT request_id FROM t_claim), 'i19-worker', (SELECT version_id FROM t_claim));
SELECT pg_temp.ok('D26 claim gives the manual request with the published version; queued after ODS while running; not claimed twice',
  (SELECT request_id FROM t_claim) = (SELECT v FROM t_id WHERE k = 'rq1')
  AND (SELECT version_id FROM t_claim) = (SELECT v FROM t_id WHERE k = 'v2')
  AND etl.dm_mart_enqueue_after_ods('i19t_manager') = 1
  AND NOT EXISTS (SELECT 1 FROM etl.dm_mart_run_request_claim('i19-worker')));
SELECT etl.dm_mart_run_finish((SELECT v FROM t_id WHERE k = 'run1'), true, 0, 3, NULL, NULL, NULL);
DROP TABLE t_claim;
CREATE TEMP TABLE t_claim AS SELECT * FROM etl.dm_mart_run_request_claim('i19-worker');
INSERT INTO t_id SELECT 'run2', etl.dm_mart_run_begin((SELECT request_id FROM t_claim), 'i19-worker', (SELECT version_id FROM t_claim));
SELECT etl.dm_mart_run_finish((SELECT v FROM t_id WHERE k = 'run2'), false, NULL, NULL, 'DM_KEY_NOT_UNIQUE',
  'Key of source a is not unique', '{"alias": "a", "keys": ["id=1"]}'::jsonb);
SELECT pg_temp.ok('D27 run results: SUCCESS then FAILED with diagnostic; status view shows the last run',
  (SELECT status FROM etl.dm_mart_run_request WHERE request_id = (SELECT v FROM t_id WHERE k = 'rq1')) = 'SUCCESS'
  AND (SELECT trigger_kind FROM t_claim) = 'AFTER_ODS'
  AND (SELECT status || ':' || result_code FROM etl.dm_mart_run_request WHERE request_id = (SELECT request_id FROM t_claim))
      = 'FAILED:DM_KEY_NOT_UNIQUE'
  AND (SELECT last_run_status || ':' || (last_run_diagnostic->>'alias') FROM etl.v_dm_mart_status
        WHERE mart_id = (SELECT v FROM t_id WHERE k = 'mart')) = 'FAILED:a');

-- Lost dispatcher: a claimed and started request whose session lock is gone is closed by the next claim.
INSERT INTO t_id SELECT 'rq3', app.dm_mart_run_request_create(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'mart'));
DROP TABLE t_claim;
CREATE TEMP TABLE t_claim AS SELECT * FROM etl.dm_mart_run_request_claim('i19-lost');
SELECT etl.dm_mart_run_begin((SELECT request_id FROM t_claim), 'i19-lost', (SELECT version_id FROM t_claim));
SELECT pg_advisory_unlock(hashtext('dwh.dm_mart_run_request'), (SELECT v FROM t_id WHERE k = 'rq3')::integer);
SELECT count(*) FROM etl.dm_mart_run_request_claim('i19-worker');
SELECT pg_temp.ok('D28 lost dispatcher: request and run FAILED DISPATCHER_LOST',
  (SELECT status || ':' || result_code FROM etl.dm_mart_run_request WHERE request_id = (SELECT v FROM t_id WHERE k = 'rq3'))
    = 'FAILED:DISPATCHER_LOST'
  AND (SELECT status || ':' || error_code FROM etl.dm_mart_run WHERE request_id = (SELECT v FROM t_id WHERE k = 'rq3'))
    = 'FAILED:DISPATCHER_LOST');

INSERT INTO t_id SELECT 'rq4', app.dm_mart_run_request_create(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'mart'));
SELECT app.dm_mart_run_request_cancel(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'rq4'));
SELECT pg_temp.expect_fail('D29 cancel only QUEUED',
  format('SELECT app.dm_mart_run_request_cancel(%L, %s)', repeat('a', 64), (SELECT v FROM t_id WHERE k = 'rq4')), 'Only QUEUED');
SELECT pg_temp.expect_fail('D31 user without the right refused',
  format('SELECT app.dm_mart_save(%L, NULL, %L, %L, NULL, NULL, false, true, true)', repeat('b', 64), 'i19t_x', 'x'),
  'access denied');

SELECT app.dm_mart_save(repeat('a', 64), (SELECT v FROM t_id WHERE k = 'mart'), NULL, 'Clients (test)', NULL, 'REPORT_VIEW',
  false, true, true);
SELECT pg_temp.ok('D30 AI flag off deactivates the whitelist row; changes are audited; dwh_app cannot write the configuration',
  EXISTS (SELECT 1 FROM meta.ai_report_object WHERE object_name = 'i19t_client_mart' AND NOT is_active)
  AND (SELECT count(*) FROM audit.security_event WHERE event_type LIKE 'DM\_%' AND actor_user_id = 9001) >= 10
  AND has_table_privilege('dwh_app', 'meta.dm_mart', 'SELECT') AND NOT has_table_privilege('dwh_app', 'meta.dm_mart', 'INSERT')
  AND NOT has_table_privilege('dwh_etl', 'etl.dm_mart_run', 'INSERT')
  AND NOT has_function_privilege('dwh_app', 'etl.dm_mart_run_request_claim(text)', 'EXECUTE')
  AND NOT has_function_privilege('dwh_etl', 'app.dm_mart_save(text, bigint, text, text, text, text, boolean, boolean, boolean)', 'EXECUTE'));

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
SELECT pg_advisory_unlock_all();
