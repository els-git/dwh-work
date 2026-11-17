-- I18 test of 021: STG and ODS tables created and changed on publication from the UI (plan, impact, confirmation,
-- shared tables, value checks of a retype, runs, proposals). One transaction, ROLLED BACK.
-- For E0 (sandbox with fixtures) and E1 (restored copy), never the working dwh.
-- app.integration_admin_session_user() is replaced inside the transaction (as in 008/012).
-- Run as the schema owner: psql -v ON_ERROR_STOP=1 -f 021_object_table_ui_ddl_test.sql
-- Expected last line: ALL_TESTS_PASS (labels X01-X19, X11b, X18b: 21 checks).
BEGIN;

CREATE FUNCTION pg_temp.expect_fail(label text, stmt text, pattern text) RETURNS void
LANGUAGE plpgsql AS $fn$
BEGIN
  BEGIN
    EXECUTE stmt;
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM !~ pattern THEN RAISE EXCEPTION 'FAIL %: other error (%)', label, SQLERRM; END IF;
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

-- Test objects: meta.source_object has more columns in dwh than in the E0 fixture (as in the test of 012).
CREATE FUNCTION pg_temp.make_object(p_system bigint, p_name text) RETURNS bigint
LANGUAGE plpgsql AS $fn$
DECLARE
  cols text := 'source_system_id, source_schema, source_object, is_active';
  vals text := format('%s, %L, %L, true', p_system, 'i18_021_test', p_name);
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
      ELSE quote_literal('i18t_' || p_system || '_' || p_name) END;
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
      v := CASE p_token_hash WHEN repeat('a', 64) THEN %s END;
      IF v IS NULL THEN RAISE EXCEPTION 'Authentication required'; END IF;
      PERFORM set_config('dwh.actor_user_id', v::text, true);
      PERFORM set_config('dwh.security_source', 'INTEGRATION_CONTROL', true);
      RETURN v;
    END; $b$ $f$, v_admin);
END
$setup$;

-- LOAD field: target column, source column, target type.
CREATE FUNCTION pg_temp.f(p_col text, p_type text DEFAULT 'TEXT', p_policy text DEFAULT 'AUTHORITATIVE') RETURNS jsonb
LANGUAGE sql AS $fn$
  SELECT jsonb_build_object('target_column', p_col, 'source_column', p_col, 'source_type', p_type,
    'target_type', p_type, 'conversion', 'NONE', 'write_policy', p_policy, 'allow_explicit_null', false) $fn$;

CREATE FUNCTION pg_temp.ldoc(p_table text, p_fields jsonb, p_key jsonb) RETURNS jsonb
LANGUAGE sql AS $fn$
  SELECT jsonb_build_object('target_schema', 'stg', 'target_table', p_table, 'fields', p_fields, 'key', p_key) $fn$;

-- ODS field copied from an STG column.
CREATE FUNCTION pg_temp.o(p_col text, p_type text DEFAULT 'TEXT') RETURNS jsonb
LANGUAGE sql AS $fn$
  SELECT jsonb_build_object('target_column', p_col, 'transform', 'COPY', 'stg_column', p_col, 'stg_type', p_type,
    'target_type', p_type, 'conversion', 'NONE', 'param', NULL, 'write_policy', 'AUTHORITATIVE',
    'allow_explicit_null', false) $fn$;

-- Save a LOAD draft, record a successful validation and return the revision.
CREATE FUNCTION pg_temp.load_draft(p_object bigint, p_doc jsonb) RETURNS integer
LANGUAGE plpgsql AS $fn$
DECLARE
  v_rev integer := (SELECT draft_revision FROM meta.object_config_version WHERE source_object_id = p_object AND status = 'DRAFT');
BEGIN
  v_rev := app.integration_object_config_save_draft(repeat('a', 64), p_object, v_rev, p_doc);
  PERFORM app.integration_object_config_record_validation(repeat('a', 64), p_object, v_rev, true, '[]');
  RETURN v_rev;
END
$fn$;

CREATE FUNCTION pg_temp.ods_draft(p_object bigint, p_doc jsonb) RETURNS integer
LANGUAGE plpgsql AS $fn$
DECLARE
  v_rev integer := (SELECT draft_revision FROM meta.object_ods_version WHERE source_object_id = p_object AND status = 'DRAFT');
BEGIN
  v_rev := app.integration_ods_config_save_draft(repeat('a', 64), p_object, v_rev, p_doc);
  PERFORM app.integration_ods_config_record_validation(repeat('a', 64), p_object, v_rev, true, '[]');
  RETURN v_rev;
END
$fn$;

CREATE FUNCTION pg_temp.col_type(p_rel text, p_col text) RETURNS text
LANGUAGE sql AS $fn$
  SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a
   WHERE a.attrelid = to_regclass(p_rel) AND a.attname = p_col AND a.attnum > 0 AND NOT a.attisdropped $fn$;

CREATE FUNCTION pg_temp.pk(p_rel text) RETURNS text
LANGUAGE sql AS $fn$
  SELECT string_agg(a.attname::text, ',' ORDER BY u.o) FROM pg_constraint c
   CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY u(attnum, o)
   JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = u.attnum
  WHERE c.conrelid = to_regclass(p_rel) AND c.contype = 'p' $fn$;

DO $t$
DECLARE
  tok text := repeat('a', 64);
  src bigint;
  o1 bigint;
  o2 bigint;
  rev integer;
  j jsonb;
  pid bigint;
  v_mart bigint;
  v_mver bigint;
  v_field bigint;
  v_admin bigint := (SELECT user_id FROM app.user_account
                      WHERE is_active AND account_level IN ('SUPERADMIN', 'ADMIN') ORDER BY user_id LIMIT 1);
BEGIN
  INSERT INTO meta.source_system(code, name, source_type, description, is_active, created_at, updated_at)
  VALUES ('I18_021_TEST', 'I18 021 test source', 'POSTGRESQL', NULL, true, now(), now()) RETURNING source_system_id INTO src;
  o1 := pg_temp.make_object(src, 'clients');
  o2 := pg_temp.make_object(src, 'clients2');

  -- New STG table.
  rev := pg_temp.load_draft(o1, pg_temp.ldoc('i18t_clients',
    jsonb_build_array(pg_temp.f('id', 'BIGINT'), pg_temp.f('pin', 'TEXT(16)'), pg_temp.f('name'), pg_temp.f('amount', 'NUMERIC(12,2)')),
    '["id"]'));
  j := app.object_table_preview(tok, 'LOAD', o1);
  PERFORM pg_temp.ok('X01 preview of a missing table: CREATE TABLE with the key, no problems, no confirmation',
    NOT (j->>'table_exists')::boolean AND jsonb_array_length(j->'problems') = 0 AND NOT (j->>'needs_confirm')::boolean
    AND j->'changes'->0->>'change' = 'CREATE_TABLE' AND j->>'ddl_text' ~ 'PRIMARY KEY \(id\)'
    AND to_regclass('stg.i18t_clients') IS NULL);
  pid := app.integration_target_proposal_create(tok, o1, 'CREATE_TABLE', 'stg', 'i18t_clients', 'CREATE TABLE stg.i18t_clients (id bigint)');
  PERFORM pg_temp.expect_fail('X02 stale draft revision rejected',
    format('SELECT app.object_table_publish(%L, %L, %s, %s, false)', tok, 'LOAD', o1, rev + 1), 'changed by another user');
  j := app.object_table_publish(tok, 'LOAD', o1, rev, false);
  PERFORM pg_temp.ok('X03 publication creates the table with whitelisted types and publishes the version',
    j->>'status' = 'PUBLISHED' AND (j->>'version_no')::integer = 1
    AND pg_temp.col_type('stg.i18t_clients', 'pin') = 'character varying(16)'
    AND pg_temp.col_type('stg.i18t_clients', 'amount') = 'numeric(12,2)'
    AND pg_temp.col_type('stg.i18t_clients', 'id') = 'bigint' AND pg_temp.pk('stg.i18t_clients') = 'id'
    AND EXISTS (SELECT 1 FROM meta.object_config_current c JOIN meta.object_config_version v ON v.version_id = c.version_id
                 WHERE c.source_object_id = o1 AND v.version_no = 1));
  PERFORM pg_temp.ok('X04 the open proposal of the draft is rejected',
    (SELECT status FROM meta.target_change_proposal WHERE proposal_id = pid) = 'REJECTED'
    AND (j->>'proposals_rejected')::integer = 1);
  INSERT INTO stg.i18t_clients (id, pin, name, amount) VALUES (1, 'P1', 'Ann', 10.50), (2, 'P2', 'Bob', 7);

  -- Same fields: nothing to change.
  rev := pg_temp.load_draft(o1, pg_temp.ldoc('i18t_clients',
    jsonb_build_array(pg_temp.f('id', 'BIGINT'), pg_temp.f('pin', 'TEXT(16)'), pg_temp.f('name'), pg_temp.f('amount', 'NUMERIC(12,2)')),
    '["id"]'));
  j := app.object_table_publish(tok, 'LOAD', o1, rev, false);
  PERFORM pg_temp.ok('X05 same fields: no DDL, published', j->>'status' = 'PUBLISHED' AND jsonb_array_length(j->'statements') = 0);

  -- A new field: ADD COLUMN without confirmation.
  rev := pg_temp.load_draft(o1, pg_temp.ldoc('i18t_clients',
    jsonb_build_array(pg_temp.f('id', 'BIGINT'), pg_temp.f('pin', 'TEXT(16)'), pg_temp.f('name'), pg_temp.f('amount', 'NUMERIC(12,2)'),
                      pg_temp.f('city')), '["id"]'));
  j := app.object_table_publish(tok, 'LOAD', o1, rev, false);
  PERFORM pg_temp.ok('X06 a new field is added without confirmation, rows kept',
    j->>'status' = 'PUBLISHED' AND pg_temp.col_type('stg.i18t_clients', 'city') = 'text'
    AND (SELECT count(*) FROM stg.i18t_clients) = 2);

  -- Removed and retyped columns need a confirmation.
  rev := pg_temp.load_draft(o1, pg_temp.ldoc('i18t_clients',
    jsonb_build_array(pg_temp.f('id', 'BIGINT'), pg_temp.f('pin', 'TEXT(8)'), pg_temp.f('name'), pg_temp.f('amount', 'NUMERIC(12,2)')),
    '["id"]'));
  j := app.object_table_publish(tok, 'LOAD', o1, rev, false);
  PERFORM pg_temp.ok('X07 removed / retyped columns: NEEDS_CONFIRM, nothing changed',
    j->>'status' = 'NEEDS_CONFIRM' AND (j->>'needs_confirm')::boolean
    AND j->'changes' @> '[{"change": "REMOVED", "column": "city"}, {"change": "RETYPED", "column": "pin", "new_type": "character varying(8)"}]'
    AND pg_temp.col_type('stg.i18t_clients', 'city') = 'text'
    AND pg_temp.col_type('stg.i18t_clients', 'pin') = 'character varying(16)'
    AND EXISTS (SELECT 1 FROM meta.object_config_version WHERE source_object_id = o1 AND status = 'DRAFT'));
  j := app.object_table_publish(tok, 'LOAD', o1, rev, true);
  PERFORM pg_temp.ok('X08 with the confirmation: column dropped, type changed, values kept, published',
    j->>'status' = 'PUBLISHED' AND pg_temp.col_type('stg.i18t_clients', 'city') IS NULL
    AND pg_temp.col_type('stg.i18t_clients', 'pin') = 'character varying(8)'
    AND (SELECT string_agg(pin, ',' ORDER BY id) FROM stg.i18t_clients) = 'P1,P2');

  -- A retype that would change values is refused, nothing changes.
  rev := pg_temp.load_draft(o1, pg_temp.ldoc('i18t_clients',
    jsonb_build_array(pg_temp.f('id', 'BIGINT'), pg_temp.f('pin', 'TEXT(1)'), pg_temp.f('name'), pg_temp.f('amount', 'NUMERIC(12,0)')),
    '["id"]'));
  PERFORM pg_temp.expect_fail('X09 a retype that shortens text or rounds numbers is refused',
    format('SELECT app.object_table_publish(%L, %L, %s, %s, true)', tok, 'LOAD', o1, rev), 'rows would change');
  PERFORM pg_temp.ok('X10 after the refusal the table and the version in force are unchanged',
    pg_temp.col_type('stg.i18t_clients', 'pin') = 'character varying(8)'
    AND (SELECT v.version_no FROM meta.object_config_current c JOIN meta.object_config_version v ON v.version_id = c.version_id
          WHERE c.source_object_id = o1) = 4);

  -- A type without a PostgreSQL equivalent is a problem.
  rev := pg_temp.load_draft(o1, pg_temp.ldoc('i18t_clients',
    jsonb_build_array(pg_temp.f('id', 'BIGINT'), pg_temp.f('pin', 'TEXT(8)'), pg_temp.f('blob', 'OTHER')), '["id"]'));
  j := app.object_table_preview(tok, 'LOAD', o1);
  PERFORM pg_temp.ok('X11 a type outside the whitelist is a problem, publication refused',
    (j->>'problems') ~ 'No PostgreSQL column type for: blob');
  PERFORM pg_temp.expect_fail('X11b publication with a problem refused',
    format('SELECT app.object_table_publish(%L, %L, %s, %s, true)', tok, 'LOAD', o1, rev), 'cannot be changed');

  -- Key change.
  rev := pg_temp.load_draft(o1, pg_temp.ldoc('i18t_clients',
    jsonb_build_array(pg_temp.f('id', 'BIGINT'), pg_temp.f('pin', 'TEXT(8)'), pg_temp.f('name'), pg_temp.f('amount', 'NUMERIC(12,2)')),
    '["pin"]'));
  j := app.object_table_publish(tok, 'LOAD', o1, rev, false);
  PERFORM pg_temp.ok('X12 key change needs a confirmation', j->>'status' = 'NEEDS_CONFIRM'
    AND j->'changes' @> '[{"change": "KEY_CHANGED", "old_key": ["id"], "new_key": ["pin"]}]' AND pg_temp.pk('stg.i18t_clients') = 'id');
  j := app.object_table_publish(tok, 'LOAD', o1, rev, true);
  PERFORM pg_temp.ok('X13 with the confirmation the key is replaced, the old key column may be NULL',
    j->>'status' = 'PUBLISHED' AND pg_temp.pk('stg.i18t_clients') = 'pin'
    AND NOT (SELECT attnotnull FROM pg_attribute WHERE attrelid = 'stg.i18t_clients'::regclass AND attname = 'id'));

  -- A table shared with another object: its columns stay, no retype of them, no key change.
  rev := pg_temp.load_draft(o2, pg_temp.ldoc('i18t_clients',
    jsonb_build_array(pg_temp.f('pin', 'TEXT(8)', 'INSERT_ONLY'), pg_temp.f('name', 'TEXT', 'INSERT_ONLY')), '["pin"]'));
  PERFORM app.object_table_publish(tok, 'LOAD', o2, rev, false);
  rev := pg_temp.load_draft(o1, pg_temp.ldoc('i18t_clients',
    jsonb_build_array(pg_temp.f('id', 'BIGINT'), pg_temp.f('pin', 'TEXT(8)'), pg_temp.f('amount', 'NUMERIC(12,2)')), '["pin"]'));
  j := app.object_table_preview(tok, 'LOAD', o1);
  PERFORM pg_temp.ok('X14 a column written by another object is KEPT, not dropped',
    jsonb_array_length(j->'problems') = 0 AND j->'changes' @> '[{"change": "KEPT", "column": "name"}]'
    AND NOT (j->'changes' @> '[{"change": "REMOVED", "column": "name"}]'));
  rev := pg_temp.load_draft(o1, pg_temp.ldoc('i18t_clients',
    jsonb_build_array(pg_temp.f('id', 'BIGINT'), pg_temp.f('pin', 'TEXT(8)'), pg_temp.f('name', 'TEXT(4)')), '["id"]'));
  j := app.object_table_preview(tok, 'LOAD', o1);
  PERFORM pg_temp.ok('X15 retype of a shared column and key change of a shared table are problems',
    (j->>'problems') ~ 'Column name is written by another object' AND (j->>'problems') ~ 'its key cannot change');

  -- ODS: new table, then the impact of STG and ODS changes (ODS configurations, DM marts).
  rev := pg_temp.load_draft(o1, pg_temp.ldoc('i18t_clients',
    jsonb_build_array(pg_temp.f('id', 'BIGINT'), pg_temp.f('pin', 'TEXT(8)'), pg_temp.f('amount', 'NUMERIC(12,2)')), '["pin"]'));
  PERFORM app.object_table_publish(tok, 'LOAD', o1, rev, true);
  rev := pg_temp.ods_draft(o1, jsonb_build_object('target_table', 'i18t_clients',
    'fields', jsonb_build_array(pg_temp.o('pin', 'TEXT(8)'), pg_temp.o('amount', 'NUMERIC(12,2)')), 'key', '["pin"]'::jsonb));
  j := app.object_table_publish(tok, 'ODS', o1, rev, false);
  PERFORM pg_temp.ok('X16 ODS publication creates ods table and publishes the ODS version',
    j->>'status' = 'PUBLISHED' AND pg_temp.pk('ods.i18t_clients') = 'pin'
    AND pg_temp.col_type('ods.i18t_clients', 'amount') = 'numeric(12,2)'
    AND EXISTS (SELECT 1 FROM meta.object_ods_current WHERE source_object_id = o1));
  -- A table created by the owner directly (as packages 009 and 013 did) gets the default privileges of the schema.
  CREATE TABLE stg.i18t_ref (x integer);
  CREATE TABLE ods.i18t_ref (x integer);
  PERFORM pg_temp.ok('X19 new tables get the privileges of a table created by a package: dwh_etl writes, dwh_app has none',
    (SELECT relacl FROM pg_class WHERE oid = 'stg.i18t_clients'::regclass)
      IS NOT DISTINCT FROM (SELECT relacl FROM pg_class WHERE oid = 'stg.i18t_ref'::regclass)
    AND (SELECT relacl FROM pg_class WHERE oid = 'ods.i18t_clients'::regclass)
      IS NOT DISTINCT FROM (SELECT relacl FROM pg_class WHERE oid = 'ods.i18t_ref'::regclass)
    AND (SELECT relowner FROM pg_class WHERE oid = 'ods.i18t_clients'::regclass) = current_user::regrole::oid
    AND has_table_privilege('dwh_etl', 'stg.i18t_clients', 'SELECT, INSERT, UPDATE, DELETE')
    AND has_table_privilege('dwh_etl', 'ods.i18t_clients', 'SELECT, INSERT, UPDATE, DELETE')
    AND NOT has_table_privilege('dwh_app', 'stg.i18t_clients', 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE')
    AND NOT has_table_privilege('dwh_app', 'ods.i18t_clients', 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE'));
  rev := pg_temp.load_draft(o1, pg_temp.ldoc('i18t_clients',
    jsonb_build_array(pg_temp.f('id', 'BIGINT'), pg_temp.f('pin', 'TEXT(8)')), '["pin"]'));
  j := app.object_table_preview(tok, 'LOAD', o1);
  INSERT INTO meta.dm_mart (mart_code, title, created_by_user_id, updated_by_user_id)
  VALUES ('i18t_mart', 'I18 test mart', v_admin, v_admin) RETURNING mart_id INTO v_mart;
  INSERT INTO meta.dm_mart_version (mart_id, version_no, created_by_user_id) VALUES (v_mart, 1, v_admin)
  RETURNING version_id INTO v_mver;
  INSERT INTO meta.dm_mart_source (version_id, position, alias, table_name, source_kind, key_column)
  VALUES (v_mver, 1, 'g', 'i18t_clients', 'GRAIN', 'pin');
  INSERT INTO meta.dm_mart_field (version_id, position, field_name, data_type, is_key) VALUES (v_mver, 1, 'amount', 'numeric', false)
  RETURNING field_id INTO v_field;
  INSERT INTO meta.dm_mart_field_source (field_id, priority_no, source_alias, source_column) VALUES (v_field, 1, 'g', 'amount');
  PERFORM pg_temp.ok('X17 removing an STG column lists the ODS configuration that reads it',
    j->'impact'->'ods_configs' @> jsonb_build_array(jsonb_build_object('source_object_id', o1, 'columns', '["amount"]'::jsonb)));
  rev := pg_temp.ods_draft(o1, jsonb_build_object('target_table', 'i18t_clients',
    'fields', jsonb_build_array(pg_temp.o('pin', 'TEXT(8)')), 'key', '["pin"]'::jsonb));
  j := app.object_table_preview(tok, 'ODS', o1);
  PERFORM pg_temp.ok('X18 removing an ODS column lists the DM mart version that reads it',
    j->'impact'->'marts' @> '[{"mart_code": "i18t_mart", "version_no": 1, "columns": ["amount"]}]');
  INSERT INTO etl.object_run_request (source_object_id, run_kind, status, requested_by_user_id, requested_by_username,
                                      claimed_by, claimed_at, started_at)
  VALUES (o1, 'RUN', 'RUNNING', v_admin, 'test', 'test-worker', now(), now());
  PERFORM pg_temp.expect_fail('X18b publication while the object is being loaded refused',
    format('SELECT app.object_table_publish(%L, %L, %s, %s, true)', tok, 'ODS', o1, rev), 'being loaded');
END
$t$;

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
