-- Rollback of 032 (I09): removes the schedules and the new columns of the run queue. It refuses when schedules or
-- scheduled / repeated requests exist (they would be lost); then restore from the copy pre-032.
-- Run as dwh_admin:  psql -1 -v ON_ERROR_STOP=1 -f 032_object_schedule.down.sql
DO $pre$
BEGIN
  IF EXISTS (SELECT 1 FROM etl.object_schedule)
     OR EXISTS (SELECT 1 FROM etl.object_run_request WHERE trigger_kind <> 'MANUAL') THEN
    RAISE EXCEPTION 'STOP: schedules or scheduled requests exist; restore from the copy pre-032';
  END IF;
END
$pre$;
DROP FUNCTION etl.object_schedule_fire(text);
DROP FUNCTION app.object_schedule_list(text);
DROP FUNCTION app.object_schedule_save(text, bigint, boolean, text, integer, text, integer[], text, integer, integer);
DROP FUNCTION etl.object_schedule_ready(bigint);
DROP FUNCTION etl.object_schedule_next(text, integer, time, smallint[], timestamptz);
DROP TABLE etl.object_schedule_event;
DROP INDEX etl.object_run_request_retry_once;
ALTER TABLE etl.object_run_request
  DROP CONSTRAINT object_run_request_trigger,
  DROP COLUMN retry_of_request_id,
  DROP COLUMN attempt,
  DROP COLUMN schedule_id,
  DROP COLUMN trigger_kind;
DROP TABLE etl.object_schedule;
SELECT 'ROLLBACK_032_DONE' AS result;
