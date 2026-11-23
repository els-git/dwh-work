-- I09 test of 032 (E0 sandbox, E1 copy), run after the package as the owner (dwh_admin):
--   psql -v ON_ERROR_STOP=1 -f 032_object_schedule_test.sql
-- One transaction, ROLLED BACK. Inside it app.integration_run_admin_session_user() and etl.object_schedule_ready()
-- are replaced (as in the tests of 015): token 'a'x64 = an active admin, 'b'x64 = refused; the test objects count
-- as READY except the one marked not ready. Errors are compared by SQLSTATE (the server writes messages in Russian).
-- Expected last line: ALL_TESTS_PASS (S01-S12, S08b).
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

-- one catalog row: the given values plus every other required column without a default; returns the key
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
      WHEN a.attname = 'object_type' THEN quote_literal('TABLE')
      WHEN a.attname = 'load_strategy' THEN quote_literal('FULL')
      WHEN a.typ IN ('integer', 'bigint', 'smallint', 'numeric') THEN '1'
      WHEN a.typ = 'boolean' THEN 'false'
      WHEN a.typ LIKE 'timestamp%' OR a.typ = 'date' THEN 'now()'
      WHEN a.typ IN ('jsonb', 'json') THEN quote_literal('{}')
      ELSE quote_literal('i0932')
    END;
  END LOOP;
  EXECUTE format('INSERT INTO %s (%s) VALUES (%s) RETURNING %I', p_table, cols, vals, p_key) INTO v;
  RETURN v;
END
$fn$;

DO $setup$
DECLARE
  v_admin bigint := (SELECT user_id FROM app.user_account
                      WHERE is_active AND account_level IN ('SUPERADMIN', 'ADMIN') ORDER BY user_id LIMIT 1);
  v_src bigint;
  o1 bigint; o2 bigint; o3 bigint;
BEGIN
  IF v_admin IS NULL THEN RAISE EXCEPTION 'FAIL setup: no active admin user'; END IF;
  EXECUTE format($f$
    CREATE OR REPLACE FUNCTION app.integration_run_admin_session_user(p_token_hash text)
     RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog', 'app', 'audit' AS $b$
    DECLARE v bigint;
    BEGIN
      v := CASE p_token_hash WHEN repeat('a', 64) THEN %s END;
      IF v IS NULL THEN RAISE EXCEPTION 'Integration run access denied'; END IF;
      PERFORM set_config('dwh.actor_user_id', v::text, true);
      PERFORM set_config('dwh.actor_username', 'i09-test', true);
      RETURN v;
    END; $b$ $f$, v_admin);
  v_src := pg_temp.make_row('meta.source_system', 'source_system_id',
            jsonb_build_object('code', 'I0932_SRC', 'name', 'I09 test source', 'is_active', true,
              'source_type', coalesce((SELECT source_type FROM meta.source_system ORDER BY source_system_id LIMIT 1), 'POSTGRESQL')));
  o1 := pg_temp.make_row('meta.source_object', 'source_object_id',
          jsonb_build_object('source_system_id', v_src, 'source_schema', 'i0932', 'source_object', 'ready_one', 'is_active', true));
  o2 := pg_temp.make_row('meta.source_object', 'source_object_id',
          jsonb_build_object('source_system_id', v_src, 'source_schema', 'i0932', 'source_object', 'ready_two', 'is_active', true));
  o3 := pg_temp.make_row('meta.source_object', 'source_object_id',
          jsonb_build_object('source_system_id', v_src, 'source_schema', 'i0932', 'source_object', 'not_ready', 'is_active', true));
  EXECUTE format($f$
    CREATE OR REPLACE FUNCTION etl.object_schedule_ready(p_source_object_id bigint)
     RETURNS boolean LANGUAGE sql STABLE SET search_path TO 'pg_catalog' AS $b$
      SELECT p_source_object_id IN (%s, %s) $b$ $f$, o1, o2);
  PERFORM set_config('t0932.o', concat_ws(',', o1, o2, o3), true);
END
$setup$;

DO $t$
DECLARE
  tok text := repeat('a', 64);
  o bigint[] := string_to_array(current_setting('t0932.o'), ',')::bigint[];
  r jsonb;
  v_sid bigint;
  v_req bigint;
  v_retry bigint;
  v_n integer;
  v_next timestamptz;
  v_audit integer;
BEGIN
  -- S01 next run, Asia/Baku; 2026-11-24 is a Tuesday
  PERFORM pg_temp.ok('S01 next run: every 15 min, every 2 h, daily, weekly (Mon, Wed)',
    etl.object_schedule_next('EVERY_MINUTES', 15, NULL, NULL, timestamp '2026-11-24 10:07' AT TIME ZONE 'Asia/Baku')
      = timestamp '2026-11-24 10:15' AT TIME ZONE 'Asia/Baku'
    AND etl.object_schedule_next('EVERY_MINUTES', 15, NULL, NULL, timestamp '2026-11-24 10:15' AT TIME ZONE 'Asia/Baku')
      = timestamp '2026-11-24 10:30' AT TIME ZONE 'Asia/Baku'
    AND etl.object_schedule_next('EVERY_HOURS', 2, NULL, NULL, timestamp '2026-11-24 10:07' AT TIME ZONE 'Asia/Baku')
      = timestamp '2026-11-24 12:00' AT TIME ZONE 'Asia/Baku'
    AND etl.object_schedule_next('DAILY', NULL, time '09:30', NULL, timestamp '2026-11-24 10:07' AT TIME ZONE 'Asia/Baku')
      = timestamp '2026-11-25 09:30' AT TIME ZONE 'Asia/Baku'
    AND etl.object_schedule_next('DAILY', NULL, time '11:00', NULL, timestamp '2026-11-24 10:07' AT TIME ZONE 'Asia/Baku')
      = timestamp '2026-11-24 11:00' AT TIME ZONE 'Asia/Baku'
    AND etl.object_schedule_next('WEEKLY', NULL, time '09:30', ARRAY[1, 3]::smallint[], timestamp '2026-11-24 10:07' AT TIME ZONE 'Asia/Baku')
      = timestamp '2026-11-25 09:30' AT TIME ZONE 'Asia/Baku'
    AND etl.object_schedule_next('WEEKLY', NULL, time '09:30', ARRAY[2]::smallint[], timestamp '2026-11-24 10:07' AT TIME ZONE 'Asia/Baku')
      = timestamp '2026-12-01 09:30' AT TIME ZONE 'Asia/Baku');

  PERFORM pg_temp.ok('S02 refused: other session, minutes below 15, bad time, weekly without days, retries above 3',
    pg_temp.err(format('SELECT app.object_schedule_save(%L, %s, true, %L, 30, NULL, NULL, %L, 0, 15)', repeat('b', 64), o[1], 'EVERY_MINUTES', 'RUN')) LIKE 'P0001:%'
    AND pg_temp.err(format('SELECT app.object_schedule_save(%L, %s, true, %L, 5, NULL, NULL, %L, 0, 15)', tok, o[1], 'EVERY_MINUTES', 'RUN')) LIKE 'P0001:%'
    AND pg_temp.err(format('SELECT app.object_schedule_save(%L, %s, true, %L, NULL, %L, NULL, %L, 0, 15)', tok, o[1], 'DAILY', '25:00', 'RUN')) LIKE 'P0001:%'
    AND pg_temp.err(format('SELECT app.object_schedule_save(%L, %s, true, %L, NULL, %L, ARRAY[]::integer[], %L, 0, 15)', tok, o[1], 'WEEKLY', '09:00', 'RUN')) LIKE 'P0001:%'
    AND pg_temp.err(format('SELECT app.object_schedule_save(%L, %s, true, %L, 30, NULL, NULL, %L, 4, 15)', tok, o[1], 'EVERY_MINUTES', 'RUN')) LIKE 'P0001:%'
    AND pg_temp.err(format('SELECT app.object_schedule_save(%L, %s, true, %L, 30, NULL, NULL, %L, 0, 15)', tok, o[1], 'EVERY_MINUTES', 'CONTINUE_ODS')) LIKE 'P0001:%');

  SELECT count(*) INTO v_audit FROM audit.security_event WHERE event_type = 'OBJECT_SCHEDULE_SAVED';
  r := app.object_schedule_save(tok, o[1], true, 'EVERY_MINUTES', 30, NULL, NULL, 'RUN', 1, 10);
  SELECT schedule_id, next_run_at INTO v_sid, v_next FROM etl.object_schedule WHERE source_object_id = o[1];
  PERFORM pg_temp.ok('S03 enabled schedule saved: next run in the future, owner, audit event, no request created',
    (r ->> 'enabled')::boolean AND v_next > clock_timestamp() AND v_next <= clock_timestamp() + interval '30 minutes'
    AND (SELECT owner_username FROM etl.object_schedule WHERE schedule_id = v_sid) = 'i09-test'
    AND (SELECT count(*) FROM audit.security_event WHERE event_type = 'OBJECT_SCHEDULE_SAVED') = v_audit + 1
    AND NOT EXISTS (SELECT 1 FROM etl.object_run_request WHERE source_object_id = o[1]));

  -- (a check reading the table goes in its own statement: a statement does not see rows written by a function it calls)
  r := app.object_schedule_save(tok, o[3], false, 'DAILY', NULL, '03:00', NULL, 'RUN', 0, 15);
  PERFORM pg_temp.ok('S04 an object without READY configuration cannot get an enabled schedule; a disabled one can',
    pg_temp.err(format('SELECT app.object_schedule_save(%L, %s, true, %L, NULL, %L, NULL, %L, 0, 15)', tok, o[3], 'DAILY', '03:00', 'RUN')) LIKE 'P0001:%'
    AND (r ->> 'enabled')::boolean = false
    AND (SELECT next_run_at IS NULL FROM etl.object_schedule WHERE source_object_id = o[3]));

  -- missed for two hours: one run only, next run counted from now
  UPDATE etl.object_schedule SET next_run_at = clock_timestamp() - interval '2 hours' WHERE schedule_id = v_sid;
  v_n := etl.object_schedule_fire('i09-test-worker');
  SELECT request_id INTO v_req FROM etl.object_run_request WHERE source_object_id = o[1] AND trigger_kind = 'SCHEDULED';
  PERFORM pg_temp.ok('S05 due schedule: one SCHEDULED request by the owner, event QUEUED, next run after now',
    v_n = 1 AND v_req IS NOT NULL
    AND (SELECT count(*) FROM etl.object_run_request WHERE source_object_id = o[1]) = 1
    AND (SELECT requested_by_username = 'i09-test' AND attempt = 1 AND run_kind = 'RUN' AND status = 'QUEUED'
           AND schedule_id = v_sid FROM etl.object_run_request WHERE request_id = v_req)
    AND EXISTS (SELECT 1 FROM etl.object_schedule_event WHERE schedule_id = v_sid AND event = 'QUEUED' AND request_id = v_req)
    AND (SELECT next_run_at > clock_timestamp() AND last_fired_at IS NOT NULL FROM etl.object_schedule WHERE schedule_id = v_sid)
    AND EXISTS (SELECT 1 FROM audit.security_event WHERE event_type = 'OBJECT_RUN_REQUEST_CREATED'
                 AND entity_key = o[1]::text AND details ->> 'trigger_kind' = 'SCHEDULED'));

  PERFORM pg_temp.ok('S06 a second call right after: nothing is due', etl.object_schedule_fire('i09-test-worker') = 0);

  UPDATE etl.object_schedule SET next_run_at = clock_timestamp() - interval '1 minute' WHERE schedule_id = v_sid;
  v_n := etl.object_schedule_fire('i09-test-worker');
  PERFORM pg_temp.ok('S07 object busy: run skipped and recorded, no second request, next run moved',
    v_n = 0
    AND (SELECT count(*) FROM etl.object_run_request WHERE source_object_id = o[1]) = 1
    AND EXISTS (SELECT 1 FROM etl.object_schedule_event WHERE schedule_id = v_sid AND event = 'SKIPPED_BUSY')
    AND (SELECT next_run_at > clock_timestamp() FROM etl.object_schedule WHERE schedule_id = v_sid));

  -- the scheduled request failed 20 minutes ago (delay 10, one retry allowed)
  UPDATE etl.object_run_request SET status = 'FAILED', finished_at = clock_timestamp() - interval '20 minutes',
         result_code = 'DATABASE_ERROR' WHERE request_id = v_req;
  v_n := etl.object_schedule_fire('i09-test-worker');
  SELECT request_id INTO v_retry FROM etl.object_run_request WHERE retry_of_request_id = v_req;
  PERFORM pg_temp.ok('S08 failed scheduled request repeated once: RETRY, attempt 2, event RETRY_QUEUED',
    v_n = 1 AND v_retry IS NOT NULL
    AND (SELECT trigger_kind = 'RETRY' AND attempt = 2 AND schedule_id = v_sid AND status = 'QUEUED'
           FROM etl.object_run_request WHERE request_id = v_retry)
    AND EXISTS (SELECT 1 FROM etl.object_schedule_event WHERE event = 'RETRY_QUEUED' AND request_id = v_retry));
  PERFORM pg_temp.ok('S08b the same failed request is not repeated twice', etl.object_schedule_fire('i09-test-worker') = 0);

  UPDATE etl.object_run_request SET status = 'FAILED', finished_at = clock_timestamp() - interval '20 minutes',
         result_code = 'DATABASE_ERROR' WHERE request_id = v_retry;
  v_n := etl.object_schedule_fire('i09-test-worker');
  PERFORM pg_temp.ok('S09 no more repeats than retry_count; a duplicate retry is refused by the base',
    v_n = 0
    AND (SELECT count(*) FROM etl.object_run_request WHERE source_object_id = o[1]) = 2
    AND pg_temp.err(format('INSERT INTO etl.object_run_request(source_object_id, run_kind, requested_by_user_id, '
          || 'requested_by_username, trigger_kind, schedule_id, attempt, retry_of_request_id) VALUES (%s, %L, 1, %L, %L, %s, 2, %s)',
          o[1], 'RUN', 'x', 'RETRY', v_sid, v_req)) LIKE '23505:%'
    AND pg_temp.err(format('INSERT INTO etl.object_run_request(source_object_id, run_kind, requested_by_user_id, '
          || 'requested_by_username, trigger_kind) VALUES (%s, %L, 1, %L, %L)', o[2], 'RUN', 'x', 'SCHEDULED')) LIKE '23514:%');

  r := app.object_schedule_save(tok, o[1], false, 'EVERY_MINUTES', 30, NULL, NULL, 'RUN', 1, 10);
  v_n := etl.object_schedule_fire('i09-test-worker');
  PERFORM pg_temp.ok('S10 disabled schedule: no next run, nothing fires; owner kept',
    (SELECT NOT is_enabled AND next_run_at IS NULL AND owner_username = 'i09-test' FROM etl.object_schedule WHERE schedule_id = v_sid)
    AND v_n = 0);

  PERFORM app.object_schedule_save(tok, o[2], true, 'WEEKLY', NULL, '06:45', ARRAY[5, 1, 1], 'FULL_RERUN', 0, 15);
  r := app.object_schedule_list(tok);
  PERFORM pg_temp.ok('S11 list: schedules with time, days, last requests with trigger and attempt; other session refused',
    r ->> 'time_zone' = 'Asia/Baku'
    AND EXISTS (SELECT 1 FROM jsonb_array_elements(r -> 'objects') x WHERE (x ->> 'source_object_id')::bigint = o[2]
                 AND x -> 'schedule' ->> 'at_time' = '06:45' AND x -> 'schedule' -> 'week_days' = '[1, 5]'::jsonb
                 AND x -> 'schedule' ->> 'run_kind' = 'FULL_RERUN' AND (x ->> 'ready')::boolean)
    AND EXISTS (SELECT 1 FROM jsonb_array_elements(r -> 'objects') x WHERE (x ->> 'source_object_id')::bigint = o[1]
                 AND x -> 'requests' -> 0 ->> 'trigger_kind' = 'RETRY' AND (x -> 'requests' -> 0 ->> 'attempt')::int = 2
                 AND x -> 'requests' -> 1 ->> 'trigger_kind' = 'SCHEDULED'
                 AND jsonb_array_length(x -> 'events') >= 3)
    AND pg_temp.err(format('SELECT app.object_schedule_list(%L)', repeat('b', 64))) LIKE 'P0001:%');

  PERFORM pg_temp.ok('S12 rights: dwh_app saves and lists, does not fire; dwh_etl fires, does not save; tables closed',
    has_function_privilege('dwh_app', 'app.object_schedule_save(text,bigint,boolean,text,integer,text,integer[],text,integer,integer)', 'EXECUTE')
    AND has_function_privilege('dwh_app', 'app.object_schedule_list(text)', 'EXECUTE')
    AND NOT has_function_privilege('dwh_app', 'etl.object_schedule_fire(text)', 'EXECUTE')
    AND has_function_privilege('dwh_etl', 'etl.object_schedule_fire(text)', 'EXECUTE')
    AND NOT has_function_privilege('dwh_etl', 'app.object_schedule_list(text)', 'EXECUTE')
    AND NOT has_table_privilege('dwh_etl', 'etl.object_schedule', 'SELECT')
    AND NOT has_table_privilege('dwh_app', 'etl.object_schedule_event', 'SELECT'));
END
$t$;

ROLLBACK;
SELECT 'ALL_TESTS_PASS' AS result;
