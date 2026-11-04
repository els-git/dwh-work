-- I05 test of 009: behaviour of stg.operation_collector_asan_pension_requests.
-- One transaction, ROLLED BACK; no row survives. For E0 and E1, never the working dwh.
-- Run as the owner: psql -v ON_ERROR_STOP=1 -f 009_target_operation_collector_asan_test.sql
-- Expected last line: ALL_TESTS_PASS (labels V01-V06: 6 checks).
BEGIN;

CREATE FUNCTION pg_temp.expect_fail(label text, stmt text, state text) RETURNS void
LANGUAGE plpgsql AS $fn$
BEGIN
  BEGIN
    EXECUTE stmt;
  EXCEPTION WHEN OTHERS THEN
    IF SQLSTATE <> state THEN RAISE EXCEPTION 'FAIL %: rejected with % instead of %', label, SQLSTATE, state; END IF;
    RAISE NOTICE 'PASS %: rejected (%)', label, SQLSTATE;
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

DO $t$
BEGIN
  INSERT INTO stg.operation_collector_asan_pension_requests
    (id, pin, name, surname, meta_data, patronymic, birth_date, is_success, is_cache, has_meta_data, is_paid,
     asan_response_status, message, interval_days, channel, product_type, created_at)
  VALUES (-1, 'TEST0000', 'Test', 'Test', '{"k": 1}', NULL, '2000-01-01', true, false, true, false,
          'OK', 'synthetic', 30, 'TEST', 'TEST', '2026-11-04 10:00:00');
  PERFORM pg_temp.ok('V01 full synthetic row inserted',
    (SELECT count(*) FROM stg.operation_collector_asan_pension_requests WHERE id = -1) = 1);
  INSERT INTO stg.operation_collector_asan_pension_requests(id) VALUES (-2);
  PERFORM pg_temp.ok('V02 a row with only the key is accepted (other columns nullable)',
    (SELECT count(*) FROM stg.operation_collector_asan_pension_requests WHERE id = -2 AND pin IS NULL) = 1);
  PERFORM pg_temp.expect_fail('V03 duplicate key rejected',
    'INSERT INTO stg.operation_collector_asan_pension_requests(id) VALUES (-1)', '23505');
  PERFORM pg_temp.expect_fail('V04 NULL key rejected',
    'INSERT INTO stg.operation_collector_asan_pension_requests(id) VALUES (NULL)', '23502');
  PERFORM pg_temp.expect_fail('V05 pin longer than 16 rejected',
    'INSERT INTO stg.operation_collector_asan_pension_requests(id, pin) VALUES (-3, repeat(''x'', 17))', '22001');
  PERFORM pg_temp.expect_fail('V06 invalid JSON in meta_data rejected',
    'INSERT INTO stg.operation_collector_asan_pension_requests(id, meta_data) VALUES (-4, ''{bad'')', '22P02');
END
$t$;

SELECT 'ALL_TESTS_PASS' AS result;
ROLLBACK;
