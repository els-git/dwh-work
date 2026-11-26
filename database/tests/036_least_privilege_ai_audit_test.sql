-- I13 test of 036 (E0 sandbox, E1 copy), run after the package as the owner (dwh_admin):
--   psql -v ON_ERROR_STOP=1 -f 036_least_privilege_ai_audit_test.sql
-- One transaction, ROLLED BACK. Errors are compared by SQLSTATE (the dwh server writes Russian messages).
-- Expected last line: ALL_TESTS_PASS (T01-T08).
BEGIN;

CREATE FUNCTION pg_temp.ok(label text, cond boolean) RETURNS void
LANGUAGE plpgsql AS $fn$
BEGIN
  IF cond IS NOT TRUE THEN RAISE EXCEPTION 'FAIL %', label; END IF;
  RAISE NOTICE 'PASS %', label;
END
$fn$;

-- runs one statement as the given role and returns the SQLSTATE or NO ERROR
CREATE FUNCTION pg_temp.try_as(p_role text, p_sql text) RETURNS text
LANGUAGE plpgsql AS $fn$
BEGIN
  EXECUTE format('SET LOCAL ROLE %I', p_role);
  EXECUTE p_sql;
  RESET ROLE;
  RETURN 'NO ERROR';
EXCEPTION WHEN OTHERS THEN
  RETURN SQLSTATE;
END
$fn$;
GRANT EXECUTE ON FUNCTION pg_temp.try_as(text, text) TO dwh_etl, dwh_app;

-- a call of the first overload of a function with NULL arguments (the privilege is checked before the body)
CREATE FUNCTION pg_temp.null_call(p_schema text, p_name text) RETURNS text
LANGUAGE sql AS $fn$
  SELECT format('SELECT %s(%s)', p.oid::regproc::text,
                coalesce((SELECT string_agg('NULL::' || format_type(t, NULL), ', ' ORDER BY o)
                            FROM unnest(p.proargtypes) WITH ORDINALITY AS a(t, o)), ''))
    FROM pg_proc p JOIN pg_namespace s ON s.oid = p.pronamespace
   WHERE s.nspname = p_schema AND p.proname = p_name ORDER BY p.oid LIMIT 1;
$fn$;

DO $t$
DECLARE
  v_obj bigint; v_ex bigint; v_before bigint; v_after bigint; e record; r text;
  v_perm text := (SELECT permission_code FROM app.permission WHERE is_active ORDER BY permission_code LIMIT 1);
  v_user bigint := (SELECT user_id FROM app.user_account ORDER BY user_id LIMIT 1);
BEGIN
  SELECT coalesce(max(security_event_id), 0) INTO v_before FROM audit.security_event;

  INSERT INTO meta.ai_report_object (schema_name, object_name, title, required_permission_code, updated_by_user_id)
  VALUES ('dm', 'i13_036_test_object', 'I13 036 test', coalesce(v_perm, 'REPORT_VIEW'), v_user)
  RETURNING object_id INTO v_obj;
  SELECT * INTO e FROM audit.security_event
   WHERE security_event_id > v_before AND event_type = 'AI_SETTINGS_CHANGED' AND entity_key = 'ai_report_object:' || v_obj;
  PERFORM pg_temp.ok('T01 new whitelist object -> AI_SETTINGS_CHANGED CREATE with object and permission',
    e.action = 'CREATE' AND e.details->>'object' = 'dm.i13_036_test_object'
    AND e.details->>'required_permission_code' = coalesce(v_perm, 'REPORT_VIEW')
    AND (v_user IS NULL OR (e.details->>'actor_user_id')::bigint = v_user));

  UPDATE meta.ai_report_object SET title = 'I13 036 test 2', updated_at = clock_timestamp() WHERE object_id = v_obj;
  SELECT * INTO e FROM audit.security_event
   WHERE security_event_id > v_before AND entity_key = 'ai_report_object:' || v_obj AND action = 'UPDATE';
  PERFORM pg_temp.ok('T02 change -> UPDATE event with the changed fields only', e.details->'changed_fields' = '["title"]'::jsonb);

  INSERT INTO meta.ai_example (object_id, question, sql_text, updated_by_user_id)
  VALUES (v_obj, 'question i13 036 secret text', 'SELECT 36 AS i13_secret_sql', v_user) RETURNING example_id INTO v_ex;
  SELECT * INTO e FROM audit.security_event
   WHERE security_event_id > v_before AND entity_key = 'ai_example:' || v_ex;
  PERFORM pg_temp.ok('T03 example -> event without the question and the SQL text',
    e.security_event_id IS NOT NULL AND (e.details::text NOT LIKE '%i13_secret_sql%')
    AND (e.details::text NOT LIKE '%secret text%') AND (e.details->>'object_id')::bigint = v_obj);

  INSERT INTO meta.ai_glossary (term, meaning, object_id, updated_by_user_id) VALUES ('t036', 'meaning 036', v_obj, v_user);
  INSERT INTO meta.ai_value_column (object_id, column_name, updated_by_user_id) VALUES (v_obj, 'col_036', v_user);
  SELECT count(*) INTO v_after FROM audit.security_event
   WHERE security_event_id > v_before AND event_type = 'AI_SETTINGS_CHANGED';
  PERFORM pg_temp.ok('T04 glossary term and value column are audited too (5 events in all)', v_after = 5);

  r := pg_temp.try_as('dwh_etl', 'UPDATE meta.source_object SET source_object = source_object WHERE false');
  PERFORM pg_temp.ok('T05 dwh_etl cannot update meta.source_object directly (42501): ' || r, r = '42501');

  r := pg_temp.try_as('dwh_etl', 'DELETE FROM etl.object_record_change WHERE false');
  PERFORM pg_temp.ok('T06 dwh_etl cannot delete record history (42501): ' || r,
    r = '42501' AND has_table_privilege('dwh_etl', 'etl.object_record_change', 'INSERT'));

  r := pg_temp.try_as('dwh_app', pg_temp.null_call('app', 'integration_dq_rule_create'));
  PERFORM pg_temp.ok('T07 dwh_app cannot call a function of the old CRM contour (42501): ' || r, r = '42501');

  r := pg_temp.try_as('dwh_etl', pg_temp.null_call('etl', 'integration_run_request_claim'));
  PERFORM pg_temp.ok('T08 dwh_etl cannot claim from the old CRM queue (42501): ' || r, r = '42501');
END
$t$;

ROLLBACK;
SELECT 'ALL_TESTS_PASS' AS result;
