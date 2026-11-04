-- I05 test of 008: object configuration drafts, validation, publication, drift, proposals.
-- Everything runs in one transaction and is ROLLED BACK; no row survives.
-- For E0 (sandbox with fixtures) and E1 (restored copy), never the working dwh.
-- As in the 005 test, app.integration_admin_session_user() is replaced inside the
-- transaction by a version mapping two fake tokens to an active admin and to a
-- foreign user id; ROLLBACK restores the real function. Test sources and objects
-- are created inside the transaction (pg_temp.make_object).
-- A test target table is created in schema stg inside the transaction.
-- Run as the schema owner: psql -v ON_ERROR_STOP=1 -f 008_object_config_test.sql
-- Expected last line: ALL_TESTS_PASS (labels V01-V30 and V28a: 31 checks).
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
  vals text := format('%s, %L, %L, %L', p_system, 'i04_test', p_name, p_active);
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
      ELSE quote_literal('i04t_' || p_system || '_' || p_name) END;
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

CREATE FUNCTION pg_temp.cfg(p_table text, p_fields jsonb, p_key jsonb) RETURNS jsonb
LANGUAGE sql AS $fn$ SELECT jsonb_build_object('target_schema', 'stg', 'target_table', p_table, 'fields', p_fields, 'key', p_key) $fn$;

CREATE FUNCTION pg_temp.fld(p_target text, p_source text, p_policy text DEFAULT 'AUTHORITATIVE', p_null boolean DEFAULT false)
RETURNS jsonb LANGUAGE sql AS $fn$
  SELECT jsonb_build_object('target_column', p_target, 'source_column', p_source, 'source_type', 'BIGINT',
    'target_type', 'BIGINT', 'conversion', 'NONE', 'write_policy', p_policy, 'allow_explicit_null', p_null) $fn$;

DO $t$
DECLARE
  tok text := repeat('a', 64);
  src bigint;
  leg bigint;
  o1 bigint;
  o2 bigint;
  oleg bigint;
  oin bigint;
  rev integer;
  v integer;
  vid bigint;
  vid2 bigint;
  pid bigint;
  j jsonb;
  fields jsonb := jsonb_build_array(pg_temp.fld('id', 'id'), pg_temp.fld('amount', 'amount'),
                                    pg_temp.fld('note', NULL, 'NO_WRITE'));
BEGIN
  INSERT INTO meta.source_system(code, name, source_type, description, is_active, created_at, updated_at)
  VALUES ('I05_TEST_PG', 'I05 test source', 'POSTGRESQL', NULL, true, now(), now()) RETURNING source_system_id INTO src;
  -- A legacy source: an existing one (CRM_RETAIL in a copy of dwh); only the E0 fixture
  -- has none, and its stub catalog accepts a minimal row.
  SELECT s.source_system_id INTO leg FROM meta.source_system s WHERE meta.source_is_legacy(s.source_system_id)
   ORDER BY s.source_system_id LIMIT 1;
  IF leg IS NULL THEN
    INSERT INTO meta.source_system(code, name, source_type, description, is_active, created_at, updated_at)
    VALUES ('I05_TEST_LEGACY', 'I05 legacy source', 'POSTGRESQL', NULL, true, now(), now()) RETURNING source_system_id INTO leg;
    EXECUTE 'INSERT INTO meta.column_contract(contract_id, source_system_code) VALUES (1, ''I05_TEST_LEGACY'')';
  END IF;
  o1 := pg_temp.make_object(src, 'orders', true);
  o2 := pg_temp.make_object(src, 'orders_copy', true);
  oin := pg_temp.make_object(src, 'old', false);
  SELECT source_object_id INTO oleg FROM meta.source_object WHERE source_system_id = leg AND is_active ORDER BY 1 LIMIT 1;
  IF oleg IS NULL THEN oleg := pg_temp.make_object(leg, 'legacy_table', true); END IF;

  -- Drafts.
  rev := app.integration_object_config_save_draft(tok, o1, NULL, pg_temp.cfg('i05_target', fields, '["id"]'));
  SELECT * INTO j FROM to_jsonb((SELECT x FROM meta.object_config_version x WHERE source_object_id = o1)) AS x;
  PERFORM pg_temp.ok('V01 new draft: revision 1, version 1, hash set',
    rev = 1 AND (j ->> 'version_no')::int = 1 AND j ->> 'status' = 'DRAFT' AND j ->> 'definition_hash' ~ '^[0-9a-f]{64}$');
  vid := (j ->> 'version_id')::bigint;
  PERFORM pg_temp.expect_fail('V02 save with a stale revision',
    format('SELECT app.integration_object_config_save_draft(%L, %s, %s, %L)', tok, o1, 5, pg_temp.cfg('i05_target', fields, '["id"]')));
  PERFORM pg_temp.expect_fail('V03 second draft created over an existing one',
    format('SELECT app.integration_object_config_save_draft(%L, %s, NULL, %L)', tok, o1, pg_temp.cfg('i05_target', fields, '["id"]')));
  rev := app.integration_object_config_save_draft(tok, o1, 1, pg_temp.cfg('i05_target', fields, '["id"]'));
  PERFORM pg_temp.ok('V04 save replaces fields, revision 2',
    rev = 2 AND (SELECT count(*) FROM meta.object_config_field WHERE version_id = vid) = 3);
  PERFORM pg_temp.expect_fail('V05 unknown write policy',
    format('SELECT app.integration_object_config_save_draft(%L, %s, %s, %L)', tok, o1, rev,
           pg_temp.cfg('i05_target', jsonb_build_array(pg_temp.fld('id', 'id', 'OVERWRITE')), '["id"]')));
  PERFORM pg_temp.expect_fail('V06 key column outside the field map',
    format('SELECT app.integration_object_config_save_draft(%L, %s, %s, %L)', tok, o1, rev,
           pg_temp.cfg('i05_target', fields, '["missing"]')));
  PERFORM pg_temp.expect_fail('V07 written field without a source column',
    format('SELECT app.integration_object_config_save_draft(%L, %s, %s, %L)', tok, o1, rev,
           pg_temp.cfg('i05_target', jsonb_build_array(pg_temp.fld('id', NULL)), '["id"]')));
  PERFORM pg_temp.ok('V08 rejected saves left revision 2 intact',
    (SELECT draft_revision FROM meta.object_config_version WHERE version_id = vid) = 2);

  -- Validation and publication.
  PERFORM pg_temp.expect_fail('V09 publish without validation',
    format('SELECT app.integration_object_config_publish(%L, %s, %s)', tok, o1, rev));
  PERFORM pg_temp.expect_fail('V10 validation for an old revision',
    format('SELECT app.integration_object_config_record_validation(%L, %s, %s, true, %L)', tok, o1, 1, '[]'));
  PERFORM app.integration_object_config_record_validation(tok, o1, rev, false, '[{"code": "TYPE"}]');
  PERFORM pg_temp.expect_fail('V11 publish after a failed validation',
    format('SELECT app.integration_object_config_publish(%L, %s, %s)', tok, o1, rev));
  PERFORM app.integration_object_config_record_validation(tok, o1, rev, true, '[]');
  PERFORM pg_temp.expect_fail('V12 publish when the target table does not exist',
    format('SELECT app.integration_object_config_publish(%L, %s, %s)', tok, o1, rev));
  pid := app.integration_target_proposal_create(tok, o1, 'CREATE_TABLE', 'stg', 'i05_target',
           'CREATE TABLE stg.i05_target (id bigint PRIMARY KEY, amount bigint, note bigint)');
  PERFORM pg_temp.ok('V13 proposal stored, not executed',
    pid IS NOT NULL AND to_regclass('stg.i05_target') IS NULL
    AND (SELECT status FROM meta.target_change_proposal WHERE proposal_id = pid) = 'PROPOSED');
  CREATE TABLE stg.i05_target (id bigint PRIMARY KEY, amount bigint);
  PERFORM pg_temp.expect_fail('V14 publish when a target column is missing',
    format('SELECT app.integration_object_config_publish(%L, %s, %s)', tok, o1, rev));
  ALTER TABLE stg.i05_target ADD COLUMN note bigint;
  PERFORM pg_temp.expect_fail('V15 proposal for a table that already exists',
    format('SELECT app.integration_target_proposal_create(%L, %s, %L, %L, %L, %L)', tok, o1, 'CREATE_TABLE', 'stg',
           'i05_target', 'CREATE TABLE x'));
  v := app.integration_object_config_publish(tok, o1, rev);
  PERFORM pg_temp.ok('V16 publish: version 1 PUBLISHED, current READY',
    v = 1 AND (SELECT status FROM meta.object_config_version WHERE version_id = vid) = 'PUBLISHED'
    AND (SELECT status FROM meta.object_config_current WHERE source_object_id = o1) = 'READY');
  PERFORM pg_temp.expect_fail('V17 published field is immutable',
    format('UPDATE meta.object_config_field SET write_policy = %L WHERE version_id = %s', 'INSERT_ONLY', vid));
  PERFORM pg_temp.expect_fail('V18 published version is immutable',
    format('UPDATE meta.object_config_version SET target_table = %L WHERE version_id = %s', 'other', vid));
  PERFORM pg_temp.expect_fail('V19 versions cannot be deleted',
    format('DELETE FROM meta.object_config_key_part WHERE version_id = %s', vid));

  -- Next draft, drift.
  rev := app.integration_object_config_save_draft(tok, o1, NULL, pg_temp.cfg('i05_target', fields, '["id"]'));
  SELECT version_id INTO vid2 FROM meta.object_config_version WHERE source_object_id = o1 AND status = 'DRAFT';
  PERFORM pg_temp.ok('V20 new draft is version 2 based on the version in force; current unchanged',
    (SELECT version_no = 2 AND based_on_version_id = vid FROM meta.object_config_version WHERE version_id = vid2)
    AND (SELECT version_id FROM meta.object_config_current WHERE source_object_id = o1) = vid);
  PERFORM pg_temp.expect_fail('V21 drift for a version not in force',
    format('SELECT app.integration_object_config_set_drift(%L, %s, %s, true, %L)', tok, o1, vid2, '{}'));
  PERFORM app.integration_object_config_set_drift(tok, o1, vid, true, '{"missing_columns": ["amount"]}');
  PERFORM pg_temp.ok('V22 drift sets DRIFT with details',
    (SELECT status = 'DRIFT' AND status_details ? 'missing_columns' FROM meta.object_config_current WHERE source_object_id = o1));
  PERFORM app.integration_object_config_record_validation(tok, o1, rev, true, '[]');
  PERFORM app.integration_object_config_publish(tok, o1, rev);
  PERFORM pg_temp.ok('V23 publishing version 2 switches the pointer and resets the status to READY',
    (SELECT version_id = vid2 AND status = 'READY' AND status_details = '{}'::jsonb
       FROM meta.object_config_current WHERE source_object_id = o1));

  -- Field ownership across objects (A08).
  rev := app.integration_object_config_save_draft(tok, o2, NULL, pg_temp.cfg('i05_target', fields, '["id"]'));
  PERFORM app.integration_object_config_record_validation(tok, o2, rev, true, '[]');
  PERFORM pg_temp.expect_fail('V24 second AUTHORITATIVE object for the same columns',
    format('SELECT app.integration_object_config_publish(%L, %s, %s)', tok, o2, rev));
  rev := app.integration_object_config_save_draft(tok, o2, rev, pg_temp.cfg('i05_target',
           jsonb_build_array(pg_temp.fld('id', 'id'), pg_temp.fld('amount', 'amount', 'INSERT_ONLY')), '["id"]'));
  PERFORM app.integration_object_config_record_validation(tok, o2, rev, true, '[]');
  PERFORM pg_temp.expect_fail('V25 key column still AUTHORITATIVE in another object',
    format('SELECT app.integration_object_config_publish(%L, %s, %s)', tok, o2, rev));
  PERFORM pg_temp.expect_fail('V26 nullable key',
    format('SELECT app.integration_object_config_save_draft(%L, %s, %s, %L)', tok, o2, rev, pg_temp.cfg('i05_target',
           jsonb_build_array(pg_temp.fld('id', 'id', 'INSERT_ONLY', true)), '["id"]'))
    || '; SELECT app.integration_object_config_record_validation(' || quote_literal(tok) || ', ' || o2 || ', '
    || (rev + 1) || ', true, ''[]''); SELECT app.integration_object_config_publish(' || quote_literal(tok) || ', '
    || o2 || ', ' || (rev + 1) || ')');

  -- Proposals, legacy, inactive, get, append-only validations.
  PERFORM app.integration_target_proposal_reject(tok, pid);
  PERFORM pg_temp.expect_fail('V27 proposal rejected only once',
    format('SELECT app.integration_target_proposal_reject(%L, %s)', tok, pid));
  PERFORM pg_temp.expect_fail('V28 legacy source: no draft, no UI snapshot',
    format('SELECT app.integration_object_config_save_draft(%L, %s, NULL, %L)', tok, oleg, pg_temp.cfg('x', fields, '["id"]')));
  PERFORM pg_temp.expect_fail('V28a legacy source: snapshot refused',
    format('SELECT * FROM app.integration_source_metadata_capture(%L, %s, %L, %L, %L)', tok, leg, 'db', '16',
           jsonb_build_array(jsonb_build_object('source_object_id', oleg, 'relkind', 'r', 'object_type', 'TABLE',
             'columns', '[{"ordinal_position": 1, "column_name": "id", "formatted_type": "bigint", "udt_name": "int8", "is_nullable": false}]'::jsonb))));
  PERFORM pg_temp.expect_fail('V29 inactive object: no draft',
    format('SELECT app.integration_object_config_save_draft(%L, %s, NULL, %L)', tok, oin, pg_temp.cfg('x', fields, '["id"]')));
  j := app.integration_object_config_get(tok, o1);
  PERFORM pg_temp.ok('V30 get: current version 2 READY, no draft, rejected proposal listed, not legacy',
    (j #>> '{current,version_no}')::int = 2 AND j #>> '{current,status}' = 'READY' AND j -> 'draft' = 'null'::jsonb
    AND j #>> '{proposals,0,status}' = 'REJECTED' AND (j #>> '{object,legacy}')::boolean = false
    AND jsonb_array_length(j #> '{current,definition,fields}') = 3);
END
$t$;

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
