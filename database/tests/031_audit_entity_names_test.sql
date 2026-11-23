-- I13-15 test of 031 (E0 sandbox, E1 copy), run after the package as the owner (dwh_admin):
--   psql -v ON_ERROR_STOP=1 -f 031_audit_entity_names_test.sql
-- Everything happens in one transaction that is rolled back. Expected last line: ALL_TESTS_PASS (N01-N11).
-- Catalog rows are created by the required columns of the catalog (the columns of dwh may differ from the sandbox).
BEGIN;

CREATE FUNCTION pg_temp.ok(label text, cond boolean) RETURNS void
LANGUAGE plpgsql AS $fn$
BEGIN
  IF cond IS NOT TRUE THEN RAISE EXCEPTION 'FAIL %', label; END IF;
  RAISE NOTICE 'PASS %', label;
END
$fn$;

CREATE FUNCTION pg_temp.err(p_sql text) RETURNS text
LANGUAGE plpgsql AS $fn$
BEGIN
  EXECUTE p_sql;
  RETURN 'NO ERROR';
EXCEPTION WHEN OTHERS THEN
  RETURN SQLSTATE || ': ' || SQLERRM;
END
$fn$;

-- one row of a catalog: the given values plus every other required column without a default; returns the key
CREATE FUNCTION pg_temp.make_row(p_table regclass, p_key text, p_values jsonb) RETURNS bigint
LANGUAGE plpgsql AS $fn$
DECLARE cols text := ''; vals text := ''; a record; k text; v bigint;
BEGIN
  FOR k IN SELECT jsonb_object_keys(p_values) LOOP
    cols := cols || CASE WHEN cols = '' THEN '' ELSE ', ' END || quote_ident(k);
    vals := vals || CASE WHEN vals = '' THEN '' ELSE ', ' END || coalesce(quote_literal(p_values ->> k), 'NULL');
  END LOOP;
  FOR a IN SELECT att.attname, format_type(att.atttypid, att.atttypmod) AS typ
             FROM pg_attribute att
            WHERE att.attrelid = p_table AND att.attnum > 0 AND NOT att.attisdropped
              AND att.attnotnull AND att.attidentity = '' AND att.attgenerated = ''
              AND NOT EXISTS (SELECT 1 FROM pg_attrdef d WHERE d.adrelid = att.attrelid AND d.adnum = att.attnum)
              AND NOT p_values ? att.attname::text
  LOOP
    cols := cols || ', ' || quote_ident(a.attname);
    vals := vals || ', ' || CASE
      WHEN a.attname IN ('object_type') THEN quote_literal('TABLE')
      WHEN a.attname IN ('source_type') THEN quote_literal('POSTGRESQL')
      WHEN a.attname IN ('account_level') THEN quote_literal('USER')
      WHEN a.attname IN ('auth_provider') THEN quote_literal('LOCAL')
      WHEN a.typ IN ('integer', 'bigint', 'smallint', 'numeric') THEN '1'
      WHEN a.typ = 'boolean' THEN 'false'
      WHEN a.typ LIKE 'timestamp%' OR a.typ = 'date' THEN 'now()'
      WHEN a.typ IN ('jsonb', 'json') THEN quote_literal('{}')
      ELSE quote_literal('i1315')
    END;
  END LOOP;
  EXECUTE format('INSERT INTO %s (%s) VALUES (%s) RETURNING %I', p_table, cols, vals, p_key) INTO v;
  RETURN v;
END
$fn$;

INSERT INTO app.user_account (username, display_name, account_level, auth_provider, password_hash, external_subject,
                              is_active, is_system, must_change_password)
VALUES ('i1315_admin', 'Admin', 'ADMIN', 'LOCAL', 'scrypt_v1$32768$8$1$' || repeat('a', 22) || '$' || repeat('b', 43), NULL, true, false, false),
       ('i1315_user', 'User', 'USER', 'LOCAL', 'scrypt_v1$32768$8$1$' || repeat('a', 22) || '$' || repeat('b', 43), NULL, true, false, false);
INSERT INTO app.auth_session (user_id, token_hash, expires_at)
SELECT user_id, CASE username WHEN 'i1315_admin' THEN repeat('c', 64) ELSE repeat('b', 64) END,
       clock_timestamp() + interval '1 hour'
  FROM app.user_account WHERE username IN ('i1315_admin', 'i1315_user');

DO $setup$
DECLARE v_src bigint; v_obj bigint; v_mart bigint; v_gone bigint; v_ent bigint; v_user bigint; v_max bigint;
BEGIN
  -- the source type is copied from an existing source (dwh may restrict its values)
  v_src := pg_temp.make_row('meta.source_system', 'source_system_id',
            jsonb_build_object('code', 'I1315_SRC', 'name', 'I1315 source', 'is_active', true,
              'source_type', coalesce((SELECT source_type FROM meta.source_system ORDER BY source_system_id LIMIT 1), 'POSTGRESQL')));
  v_obj := pg_temp.make_row('meta.source_object', 'source_object_id',
            jsonb_build_object('source_system_id', v_src, 'source_schema', 'public', 'source_object', 'i1315_services'));
  v_mart := pg_temp.make_row('meta.dm_mart', 'mart_id', '{"mart_code": "i1315_mart", "title": "I1315 mart"}');
  v_gone := pg_temp.make_row('meta.dm_mart', 'mart_id',
            jsonb_build_object('mart_code', 'i1315_gone', 'title', 'I1315 gone', 'deleted_at', now()::text));
  v_ent := pg_temp.make_row('meta.dm_entity', 'entity_id', '{"entity_code": "I1315_ENTITY", "title": "I1315 entity"}');
  SELECT user_id INTO v_user FROM app.user_account WHERE username = 'i1315_user';
  SELECT greatest((SELECT coalesce(max(source_object_id), 0) FROM meta.source_object),
                  (SELECT coalesce(max(user_id), 0) FROM app.user_account)) + 1000000 INTO v_max;
  INSERT INTO audit.security_event (actor_username, actor_account_level, event_type, entity_type, entity_key,
                                    action, outcome, source, details)
  VALUES ('i1315_admin', 'ADMIN', 'I1315_TEST', 'SOURCE_OBJECT', v_obj::text, 'RUN', 'SUCCESS', 'TEST', '{"password": "pw9"}'),
         ('i1315_admin', 'ADMIN', 'I1315_TEST', 'SOURCE_SYSTEM', v_src::text, 'READ', 'SUCCESS', 'TEST', '{}'),
         ('i1315_admin', 'ADMIN', 'I1315_TEST', 'DM_MART', v_mart::text, 'RUN', 'SUCCESS', 'TEST', '{}'),
         ('i1315_admin', 'ADMIN', 'I1315_TEST', 'DM_MART', v_gone::text, 'DELETE', 'SUCCESS', 'TEST', '{}'),
         ('i1315_admin', 'ADMIN', 'I1315_TEST', 'DM_ENTITY', v_ent::text, 'UPDATE', 'SUCCESS', 'TEST', '{}'),
         ('i1315_admin', 'ADMIN', 'I1315_TEST', 'USER_ACCOUNT', v_user::text, 'UPDATE', 'SUCCESS', 'TEST', '{}'),
         ('i1315_admin', 'ADMIN', 'I1315_TEST', 'SOURCE_OBJECT', v_max::text, 'RUN', 'SUCCESS', 'TEST', '{}'),
         ('i1315_admin', 'ADMIN', 'I1315_TEST', 'SOURCE_OBJECT', 'not-a-number', 'RUN', 'SUCCESS', 'TEST', '{}'),
         ('i1315_admin', 'ADMIN', 'I1315_TEST', 'AUTH', 'i1315_user', 'LOGIN', 'SUCCESS', 'TEST', '{}');
  PERFORM set_config('t1315.ids', concat_ws(',', v_obj, v_src, v_mart, v_gone, v_ent, v_user, v_max), true);
END
$setup$;

DO $t$
DECLARE r jsonb; rows jsonb; ids text[] := string_to_array(current_setting('t1315.ids'), ','); v_views integer;
BEGIN
  SELECT count(*) INTO v_views FROM audit.security_event WHERE event_type = 'AUDIT_LOG_VIEWED';
  r := app.audit_log_page(repeat('c', 64), NULL, NULL, 'I1315_TEST', NULL, NULL, NULL, NULL, 50);
  rows := r -> 'rows';
  PERFORM pg_temp.ok('N01 source object: "<source code> · <schema>.<object>", not deleted',
    EXISTS (SELECT 1 FROM jsonb_array_elements(rows) x WHERE x ->> 'entity_type' = 'SOURCE_OBJECT'
             AND x ->> 'entity_key' = ids[1] AND x ->> 'entity_name' = 'I1315_SRC · public.i1315_services'
             AND NOT (x ->> 'entity_deleted')::boolean));
  PERFORM pg_temp.ok('N02 source: its code',
    EXISTS (SELECT 1 FROM jsonb_array_elements(rows) x WHERE x ->> 'entity_type' = 'SOURCE_SYSTEM'
             AND x ->> 'entity_key' = ids[2] AND x ->> 'entity_name' = 'I1315_SRC'));
  PERFORM pg_temp.ok('N03 mart: its code; a deleted mart keeps its code and is marked deleted',
    EXISTS (SELECT 1 FROM jsonb_array_elements(rows) x WHERE x ->> 'entity_key' = ids[3] AND x ->> 'entity_type' = 'DM_MART'
             AND x ->> 'entity_name' = 'i1315_mart' AND NOT (x ->> 'entity_deleted')::boolean)
    AND EXISTS (SELECT 1 FROM jsonb_array_elements(rows) x WHERE x ->> 'entity_key' = ids[4] AND x ->> 'entity_type' = 'DM_MART'
             AND x ->> 'entity_name' = 'i1315_gone' AND (x ->> 'entity_deleted')::boolean));
  PERFORM pg_temp.ok('N04 entity: its code; user account: login',
    EXISTS (SELECT 1 FROM jsonb_array_elements(rows) x WHERE x ->> 'entity_type' = 'DM_ENTITY'
             AND x ->> 'entity_key' = ids[5] AND x ->> 'entity_name' = 'I1315_ENTITY')
    AND EXISTS (SELECT 1 FROM jsonb_array_elements(rows) x WHERE x ->> 'entity_type' = 'USER_ACCOUNT'
             AND x ->> 'entity_key' = ids[6] AND x ->> 'entity_name' = 'i1315_user'));
  PERFORM pg_temp.ok('N05 number without a catalog row: no name, marked deleted',
    EXISTS (SELECT 1 FROM jsonb_array_elements(rows) x WHERE x ->> 'entity_type' = 'SOURCE_OBJECT'
             AND x ->> 'entity_key' = ids[7] AND x -> 'entity_name' = 'null'::jsonb AND (x ->> 'entity_deleted')::boolean));
  PERFORM pg_temp.ok('N06 text key and other object types: no name, not deleted',
    EXISTS (SELECT 1 FROM jsonb_array_elements(rows) x WHERE x ->> 'entity_key' = 'not-a-number'
             AND x -> 'entity_name' = 'null'::jsonb AND NOT (x ->> 'entity_deleted')::boolean)
    AND EXISTS (SELECT 1 FROM jsonb_array_elements(rows) x WHERE x ->> 'entity_type' = 'AUTH'
             AND x -> 'entity_name' = 'null'::jsonb AND NOT (x ->> 'entity_deleted')::boolean));
  r := app.audit_log_page(repeat('c', 64), NULL, NULL, 'I1315_TEST', NULL, 'I1315_services', NULL, NULL, 50);
  PERFORM pg_temp.ok('N07 object filter by part of the object name, any case',
    jsonb_array_length(r -> 'rows') = 1 AND r -> 'rows' -> 0 ->> 'entity_key' = ids[1]);
  r := app.audit_log_page(repeat('c', 64), NULL, NULL, 'I1315_TEST', NULL, 'i1315_src', NULL, NULL, 50);
  PERFORM pg_temp.ok('N08 filter by the source code finds the source and its objects',
    (SELECT count(*) FROM jsonb_array_elements(r -> 'rows') x) = 2
    AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(r -> 'rows') x
                     WHERE x ->> 'entity_key' NOT IN (ids[1], ids[2])));
  r := app.audit_log_page(repeat('c', 64), NULL, NULL, 'I1315_TEST', NULL, 'not-a-num', NULL, NULL, 50);
  PERFORM pg_temp.ok('N09 filter by key and by object type works as before',
    jsonb_array_length(r -> 'rows') = 1
    AND jsonb_array_length(app.audit_log_page(repeat('c', 64), NULL, NULL, 'I1315_TEST', NULL, 'dm_entity', NULL, NULL, 50) -> 'rows') = 1);
  r := app.audit_log_page(repeat('c', 64), NULL, NULL, 'I1315_TEST', NULL, NULL, NULL, NULL, 50);
  PERFORM pg_temp.ok('N10 details still masked; each of the 6 requests above is logged once',
    position('pw9' IN r::text) = 0
    AND (SELECT count(*) FROM audit.security_event WHERE event_type = 'AUDIT_LOG_VIEWED') = v_views + 6);
  PERFORM pg_temp.ok('N11 USER level is refused; the helpers are closed to dwh_app',
    pg_temp.err($$SELECT app.audit_log_page(repeat('b', 64), NULL, NULL, NULL, NULL, NULL, NULL, NULL, 10)$$) LIKE 'P0001:%'
    AND NOT has_function_privilege('dwh_app', 'app.audit_entity_name(text,text)', 'EXECUTE')
    AND NOT has_function_privilege('dwh_app', 'app.audit_entity_search(text)', 'EXECUTE'));
END
$t$;

ROLLBACK;
SELECT 'ALL_TESTS_PASS' AS result;
