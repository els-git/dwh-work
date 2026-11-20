-- I02-03 behaviour test of 026 (run as the owner, dwh_admin; everything is rolled back).
-- A synthetic source with an ACTIVE credential, six objects and their load requests in different states.
-- Expected: T01-T12 PASS, then ALL_TESTS_PASS; nothing remains after ROLLBACK.
BEGIN;
SET LOCAL search_path = pg_catalog;

DO $t$
DECLARE
  v_user bigint := (SELECT min(user_id) FROM app.user_account);
  v_src bigint; v_other bigint;
  v_op uuid; v_ref uuid := gen_random_uuid();
  o bigint[] := '{}';
  v_obj bigint;
  i int;
BEGIN
  INSERT INTO meta.source_system(code, name, source_type)
  SELECT 'T026_SRC', 'test 026', source_type FROM meta.source_system ORDER BY source_system_id LIMIT 1
  RETURNING source_system_id INTO v_src;
  INSERT INTO meta.source_system(code, name, source_type)
  SELECT 'T026_OTHER', 'test 026 other', source_type FROM meta.source_system ORDER BY source_system_id LIMIT 1
  RETURNING source_system_id INTO v_other;
  INSERT INTO meta.source_connection(source_system_id, source_kind, connection_config, status, created_by, updated_by)
  VALUES (v_src, 'DATABASE', '{}'::jsonb, 'DRAFT', v_user, v_user);
  v_op := secret_store.begin_operation('CREATE', v_src, 'DWH_USER', v_user::text);
  PERFORM secret_store.register_pending(v_op, v_ref, 'SOURCE');
  PERFORM secret_store.finish_operation(v_op, true);
  PERFORM secret_store.activate_binding(v_op, repeat('a', 64), 'K_T026');
  FOR i IN 1..6 LOOP
    INSERT INTO meta.source_object(source_system_id, source_schema, source_object)
    VALUES (CASE WHEN i = 5 THEN v_other ELSE v_src END, 't026', 'obj' || i)
    RETURNING source_object_id INTO v_obj;
    o := o || v_obj;
  END LOOP;
  -- 1 RUN running now; 2 QUEUED; 3 CONTINUE_ODS running; 4 started 20 minutes ago; 5 object of another source;
  -- 6 FULL_RERUN running now, later cancelled
  INSERT INTO etl.object_run_request(source_object_id, run_kind, status, requested_by_user_id, requested_by_username,
                                     claimed_by, claimed_at, started_at)
  VALUES (o[1], 'RUN', 'RUNNING', v_user, 't026', 'w', clock_timestamp(), clock_timestamp()),
         (o[3], 'CONTINUE_ODS', 'RUNNING', v_user, 't026', 'w', clock_timestamp(), clock_timestamp()),
         (o[4], 'RUN', 'RUNNING', v_user, 't026', 'w', clock_timestamp() - interval '20 minutes',
          clock_timestamp() - interval '20 minutes'),
         (o[5], 'RUN', 'RUNNING', v_user, 't026', 'w', clock_timestamp(), clock_timestamp()),
         (o[6], 'FULL_RERUN', 'RUNNING', v_user, 't026', 'w', clock_timestamp(), clock_timestamp());
  INSERT INTO etl.object_run_request(source_object_id, run_kind, status, requested_by_user_id, requested_by_username)
  VALUES (o[2], 'RUN', 'QUEUED', v_user, 't026');
  PERFORM set_config('t026.objects', array_to_string(o, ','), true);
END
$t$;

CREATE TEMP TABLE t026_req ON COMMIT DROP AS
SELECT row_number() OVER (ORDER BY array_position(string_to_array(current_setting('t026.objects'), ',')::bigint[],
                                                  r.source_object_id)) AS n, r.request_id
  FROM etl.object_run_request r
 WHERE r.source_object_id = ANY (string_to_array(current_setting('t026.objects'), ',')::bigint[]);
GRANT SELECT ON t026_req TO dwh_etl;
CREATE TEMP TABLE t026_result (n int, ok boolean, note text) ON COMMIT DROP;
GRANT INSERT, SELECT ON t026_result TO dwh_etl;

SET LOCAL ROLE dwh_etl;
DO $t$
DECLARE
  rec record;
  req bigint[];
  v text;
  v_denied_before bigint;
BEGIN
  SELECT array_agg(request_id ORDER BY n) INTO req FROM t026_req;

  SELECT * INTO rec FROM etl.source_secret_read_begin('T026_SRC', req[1]);
  INSERT INTO t026_result VALUES (1, rec.operation_id IS NOT NULL AND rec.refusal IS NULL AND rec.key_id = 'K_T026',
                                  'RUNNING RUN request: credential reference given');
  INSERT INTO t026_result VALUES (2, etl.source_secret_read_finish(rec.operation_id, true),
                                  'OUTCOME of the read is written by the same role');

  SELECT refusal INTO v FROM etl.source_secret_read_begin('T026_SRC', req[1]);
  INSERT INTO t026_result VALUES (3, v = 'CREDENTIAL_ALREADY_READ', 'second read of the same request: ' || coalesce(v, 'given'));
  SELECT refusal INTO v FROM etl.source_secret_read_begin('T026_SRC', req[2]);
  INSERT INTO t026_result VALUES (4, v = 'REQUEST_NOT_RUNNING', 'QUEUED request: ' || coalesce(v, 'given'));
  SELECT refusal INTO v FROM etl.source_secret_read_begin('T026_SRC', req[3]);
  INSERT INTO t026_result VALUES (5, v = 'REQUEST_DOES_NOT_READ_SOURCE', 'CONTINUE_ODS request: ' || coalesce(v, 'given'));
  SELECT refusal INTO v FROM etl.source_secret_read_begin('T026_SRC', req[4]);
  INSERT INTO t026_result VALUES (6, v = 'REQUEST_STARTED_TOO_LONG_AGO', 'started 20 min ago: ' || coalesce(v, 'given'));
  SELECT refusal INTO v FROM etl.source_secret_read_begin('T026_SRC', req[5]);
  INSERT INTO t026_result VALUES (7, v = 'REQUEST_OF_OTHER_SOURCE', 'object of another source: ' || coalesce(v, 'given'));
  SELECT refusal INTO v FROM etl.source_secret_read_begin('T026_SRC', -1);
  INSERT INTO t026_result VALUES (8, v = 'REQUEST_NOT_FOUND', 'unknown request: ' || coalesce(v, 'given'));
  BEGIN
    PERFORM etl.source_secret_read_begin('T026_SRC');
    INSERT INTO t026_result VALUES (9, false, 'old function by code still callable by dwh_etl');
  EXCEPTION WHEN insufficient_privilege THEN
    INSERT INTO t026_result VALUES (9, true, 'old function by code: permission denied for dwh_etl');
  END;
END
$t$;
RESET ROLE;

-- request 6 is cancelled before its read (E10)
UPDATE etl.object_run_request SET status = 'CANCELLED', finished_at = clock_timestamp()
 WHERE request_id = (SELECT request_id FROM t026_req WHERE n = 6);
SET LOCAL ROLE dwh_etl;
DO $t$
DECLARE v text;
BEGIN
  SELECT refusal INTO v FROM etl.source_secret_read_begin('T026_SRC', (SELECT request_id FROM t026_req WHERE n = 6));
  INSERT INTO t026_result VALUES (10, v = 'REQUEST_NOT_RUNNING', 'cancelled request: ' || coalesce(v, 'given'));
END
$t$;
RESET ROLE;

INSERT INTO t026_result
SELECT 11, count(*) FILTER (WHERE e.code = 'DENIED_ATTEMPT') = 7
          AND count(*) FILTER (WHERE e.code = 'PERMITTED_ATTEMPT') = 1,
       'journal: 7 DENIED and 1 PERMITTED READ attempts of the test source'
  FROM secret_store.operation_event e JOIN meta.source_system s ON s.source_system_id = e.source_system_id
 WHERE s.code = 'T026_SRC' AND e.operation_type = 'READ' AND e.phase = 'ATTEMPT';
INSERT INTO t026_result
SELECT 12, count(*) = 1, 'one row in etl.object_run_request_secret_read'
  FROM etl.object_run_request_secret_read x JOIN t026_req q ON q.request_id = x.request_id;

SELECT 'T' || lpad(n::text, 2, '0') AS test, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result, note
  FROM t026_result ORDER BY n;
SELECT CASE WHEN count(*) = 12 AND bool_and(ok) THEN 'ALL_TESTS_PASS' ELSE 'TESTS_FAILED' END AS summary
  FROM t026_result;
ROLLBACK;
