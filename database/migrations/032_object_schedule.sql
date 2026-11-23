-- I09 (package 032): schedule of a single object. Plan: docs/DWH_I09_OBJECT_SCHEDULER_PLAN_APPROVED_20261123.md
-- (P1-P10 = A, user decision 23.11.2026). The dispatcher calls etl.object_schedule_fire() on every cycle; a due
-- schedule becomes an ordinary object run request (queue of package 015) marked SCHEDULED, a failed scheduled
-- request may be repeated (RETRY). Saving a schedule never starts a run.
--   etl.object_schedule          one schedule per object (P1): frequency, time, kind of run, retries, next run
--   etl.object_schedule_event    what the scheduler did: QUEUED, RETRY_QUEUED, SKIPPED_BUSY, SKIPPED_NOT_READY
--   etl.object_run_request       + trigger_kind (MANUAL / SCHEDULED / RETRY), schedule_id, attempt, retry_of_request_id
--   etl.object_schedule_next()   next run strictly after a moment, time zone Asia/Baku (P3); no grants
--   etl.object_schedule_ready()  the object can be loaded: active object and source, READY configuration; no grants
--   app.object_schedule_list(token)  schedules, last requests and events for the "Schedules" tab (dwh_app)
--   app.object_schedule_save(...)    create / change / enable / disable a schedule (dwh_app)
--   etl.object_schedule_fire(worker) due schedules and retries -> requests (dwh_etl, dispatcher)
-- Session and rights as for manual runs: app.integration_run_admin_session_user (P9).
-- Run as the owner of schemas app, etl, meta, audit (dwh_admin):  psql -1 -v ON_ERROR_STOP=1 -f 032_object_schedule.sql
DO $pre$
BEGIN
  IF to_regclass('etl.object_run_request') IS NULL OR to_regclass('meta.object_config_current') IS NULL
     OR to_regclass('meta.source_object') IS NULL OR to_regclass('meta.source_system') IS NULL THEN
    RAISE EXCEPTION 'STOP: object run queue (015) or object configuration (008) not found';
  END IF;
  IF to_regprocedure('app.integration_run_admin_session_user(text)') IS NULL
     OR to_regprocedure('audit.log_security_event(text,text,text,text,text,jsonb)') IS NULL THEN
    RAISE EXCEPTION 'STOP: app.integration_run_admin_session_user or audit.log_security_event not found';
  END IF;
  IF to_regclass('etl.object_schedule') IS NOT NULL
     OR EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'etl'
                 AND table_name = 'object_run_request' AND column_name = 'trigger_kind') THEN
    RAISE EXCEPTION 'STOP: 032 is already applied';
  END IF;
END
$pre$;

-- ------------------------------------------------------------------ tables

CREATE TABLE etl.object_schedule (
  schedule_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source_object_id bigint NOT NULL UNIQUE REFERENCES meta.source_object(source_object_id) ON DELETE RESTRICT,
  is_enabled boolean NOT NULL DEFAULT false,
  frequency text NOT NULL CHECK (frequency IN ('EVERY_MINUTES', 'EVERY_HOURS', 'DAILY', 'WEEKLY')),
  every_n integer,
  at_time time without time zone CHECK (at_time IS NULL OR extract(second FROM at_time) = 0),
  week_days smallint[],
  run_kind text NOT NULL DEFAULT 'RUN' CHECK (run_kind IN ('RUN', 'FULL_RERUN')),
  retry_count smallint NOT NULL DEFAULT 0 CHECK (retry_count BETWEEN 0 AND 3),
  retry_delay_minutes smallint NOT NULL DEFAULT 15 CHECK (retry_delay_minutes BETWEEN 5 AND 60),
  time_zone text NOT NULL DEFAULT 'Asia/Baku' CHECK (time_zone = 'Asia/Baku'),
  owner_user_id bigint,
  owner_username text CHECK (char_length(owner_username) BETWEEN 1 AND 255),
  next_run_at timestamptz,
  last_fired_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_by_user_id bigint NOT NULL,
  updated_by_username text NOT NULL CHECK (char_length(updated_by_username) BETWEEN 1 AND 255),
  CONSTRAINT object_schedule_shape CHECK (
       (frequency = 'EVERY_MINUTES' AND every_n BETWEEN 15 AND 720 AND at_time IS NULL AND week_days IS NULL)
    OR (frequency = 'EVERY_HOURS' AND every_n BETWEEN 1 AND 24 AND at_time IS NULL AND week_days IS NULL)
    OR (frequency = 'DAILY' AND every_n IS NULL AND at_time IS NOT NULL AND week_days IS NULL)
    OR (frequency = 'WEEKLY' AND every_n IS NULL AND at_time IS NOT NULL AND week_days IS NOT NULL
        AND cardinality(week_days) BETWEEN 1 AND 7 AND week_days <@ ARRAY[1, 2, 3, 4, 5, 6, 7]::smallint[])),
  CONSTRAINT object_schedule_enabled CHECK (
    NOT is_enabled OR (next_run_at IS NOT NULL AND owner_user_id IS NOT NULL AND owner_username IS NOT NULL)),
  CONSTRAINT object_schedule_disabled CHECK (is_enabled OR next_run_at IS NULL)
);
CREATE INDEX object_schedule_due ON etl.object_schedule (next_run_at) WHERE is_enabled;

ALTER TABLE etl.object_run_request
  ADD COLUMN trigger_kind text NOT NULL DEFAULT 'MANUAL' CHECK (trigger_kind IN ('MANUAL', 'SCHEDULED', 'RETRY')),
  ADD COLUMN schedule_id bigint REFERENCES etl.object_schedule(schedule_id) ON DELETE RESTRICT,
  ADD COLUMN attempt smallint NOT NULL DEFAULT 1 CHECK (attempt BETWEEN 1 AND 4),
  ADD COLUMN retry_of_request_id bigint REFERENCES etl.object_run_request(request_id) ON DELETE RESTRICT,
  ADD CONSTRAINT object_run_request_trigger CHECK (
    (trigger_kind = 'MANUAL') = (schedule_id IS NULL)
    AND (trigger_kind = 'RETRY') = (retry_of_request_id IS NOT NULL)
    AND (trigger_kind = 'RETRY') = (attempt > 1));
CREATE UNIQUE INDEX object_run_request_retry_once ON etl.object_run_request (retry_of_request_id)
  WHERE retry_of_request_id IS NOT NULL;

CREATE TABLE etl.object_schedule_event (
  event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  schedule_id bigint NOT NULL REFERENCES etl.object_schedule(schedule_id) ON DELETE RESTRICT,
  source_object_id bigint NOT NULL REFERENCES meta.source_object(source_object_id) ON DELETE RESTRICT,
  occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  event text NOT NULL CHECK (event IN ('QUEUED', 'RETRY_QUEUED', 'SKIPPED_BUSY', 'SKIPPED_NOT_READY')),
  planned_at timestamptz,
  request_id bigint REFERENCES etl.object_run_request(request_id) ON DELETE RESTRICT,
  message text CHECK (char_length(message) <= 1000),
  CONSTRAINT object_schedule_event_request CHECK ((event IN ('QUEUED', 'RETRY_QUEUED')) = (request_id IS NOT NULL))
);
CREATE INDEX object_schedule_event_schedule ON etl.object_schedule_event (schedule_id, event_id DESC);

-- ------------------------------------------------------------------ helpers (owner only)

-- The next run strictly after p_after, in Asia/Baku (no daylight saving). EVERY_MINUTES / EVERY_HOURS follow a
-- fixed grid from 2026-01-01 00:00, so the time of saving does not shift the runs.
CREATE FUNCTION etl.object_schedule_next(p_frequency text, p_every_n integer, p_at_time time, p_week_days smallint[],
                                         p_after timestamptz)
RETURNS timestamptz
LANGUAGE plpgsql STABLE SET search_path TO 'pg_catalog' AS $fn$
DECLARE
  v_local timestamp := p_after AT TIME ZONE 'Asia/Baku';
  v_step interval;
  c timestamp;
BEGIN
  IF p_frequency IN ('EVERY_MINUTES', 'EVERY_HOURS') THEN
    v_step := CASE p_frequency WHEN 'EVERY_MINUTES' THEN make_interval(mins => p_every_n)
                               ELSE make_interval(hours => p_every_n) END;
    RETURN (date_bin(v_step, v_local, timestamp '2026-01-01 00:00') + v_step) AT TIME ZONE 'Asia/Baku';
  ELSIF p_frequency = 'DAILY' THEN
    c := v_local::date + p_at_time;
    IF c <= v_local THEN c := c + interval '1 day'; END IF;
    RETURN c AT TIME ZONE 'Asia/Baku';
  ELSIF p_frequency = 'WEEKLY' THEN
    FOR i IN 0..7 LOOP
      c := (v_local::date + i) + p_at_time;
      IF c > v_local AND extract(isodow FROM c)::smallint = ANY (p_week_days) THEN
        RETURN c AT TIME ZONE 'Asia/Baku';
      END IF;
    END LOOP;
  END IF;
  RAISE EXCEPTION 'Invalid schedule: no next run';
END
$fn$;

-- The object can be loaded now (the same conditions as a manual run of package 015).
CREATE FUNCTION etl.object_schedule_ready(p_source_object_id bigint)
RETURNS boolean
LANGUAGE sql STABLE SET search_path TO 'pg_catalog', 'meta' AS $fn$
  SELECT EXISTS (
    SELECT 1 FROM meta.source_object o
      JOIN meta.source_system s ON s.source_system_id = o.source_system_id
     WHERE o.source_object_id = p_source_object_id AND o.is_active AND s.is_active
       AND EXISTS (SELECT 1 FROM meta.object_config_current c
                    WHERE c.source_object_id = o.source_object_id AND c.status = 'READY'))
$fn$;

-- ------------------------------------------------------------------ UI (dwh_app)

CREATE FUNCTION app.object_schedule_save(p_token_hash text, p_source_object_id bigint, p_enabled boolean,
  p_frequency text, p_every_n integer, p_at_time text, p_week_days integer[], p_run_kind text,
  p_retry_count integer, p_retry_delay_minutes integer)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog', 'app', 'etl', 'meta', 'audit' AS $fn$
DECLARE
  v_actor bigint;
  v_username text;
  v_freq text := upper(btrim(coalesce(p_frequency, '')));
  v_kind text := upper(btrim(coalesce(p_run_kind, 'RUN')));
  v_time time;
  v_days smallint[];
  v_n integer := p_every_n;
  v_next timestamptz;
  s etl.object_schedule;
BEGIN
  v_actor := app.integration_run_admin_session_user(p_token_hash);
  v_username := left(coalesce(nullif(current_setting('dwh.actor_username', true), ''), 'user ' || v_actor), 255);
  IF p_enabled IS NULL THEN RAISE EXCEPTION 'Invalid schedule: enabled flag is required'; END IF;
  IF NOT EXISTS (SELECT 1 FROM meta.source_object o WHERE o.source_object_id = p_source_object_id) THEN
    RAISE EXCEPTION 'Source object not found';
  END IF;
  IF v_freq NOT IN ('EVERY_MINUTES', 'EVERY_HOURS', 'DAILY', 'WEEKLY') THEN
    RAISE EXCEPTION 'Invalid schedule: unknown frequency';
  END IF;
  IF v_kind NOT IN ('RUN', 'FULL_RERUN') THEN RAISE EXCEPTION 'Invalid schedule: unknown run kind'; END IF;
  IF v_freq = 'EVERY_MINUTES' AND (v_n IS NULL OR v_n NOT BETWEEN 15 AND 720) THEN
    RAISE EXCEPTION 'Invalid schedule: minutes must be from 15 to 720';
  END IF;
  IF v_freq = 'EVERY_HOURS' AND (v_n IS NULL OR v_n NOT BETWEEN 1 AND 24) THEN
    RAISE EXCEPTION 'Invalid schedule: hours must be from 1 to 24';
  END IF;
  IF v_freq IN ('DAILY', 'WEEKLY') THEN
    v_n := NULL;
    IF coalesce(p_at_time, '') !~ '^([01][0-9]|2[0-3]):[0-5][0-9]$' THEN
      RAISE EXCEPTION 'Invalid schedule: time must be HH:MM';
    END IF;
    v_time := p_at_time::time;
  END IF;
  IF v_freq = 'WEEKLY' THEN
    IF p_week_days IS NULL OR cardinality(p_week_days) = 0
       OR EXISTS (SELECT 1 FROM unnest(p_week_days) d WHERE d IS NULL OR d NOT BETWEEN 1 AND 7) THEN
      RAISE EXCEPTION 'Invalid schedule: week days must be 1 (Monday) to 7 (Sunday)';
    END IF;
    SELECT array_agg(DISTINCT d::smallint ORDER BY d::smallint) INTO v_days FROM unnest(p_week_days) d;
  END IF;
  IF coalesce(p_retry_count, -1) NOT BETWEEN 0 AND 3 THEN
    RAISE EXCEPTION 'Invalid schedule: retries must be from 0 to 3';
  END IF;
  IF coalesce(p_retry_delay_minutes, -1) NOT BETWEEN 5 AND 60 THEN
    RAISE EXCEPTION 'Invalid schedule: retry delay must be from 5 to 60 minutes';
  END IF;
  IF p_enabled AND NOT etl.object_schedule_ready(p_source_object_id) THEN
    RAISE EXCEPTION 'Object has no published READY configuration or is not active';
  END IF;
  IF p_enabled THEN
    v_next := etl.object_schedule_next(v_freq, v_n, v_time, v_days, clock_timestamp());
  END IF;
  INSERT INTO etl.object_schedule AS x (source_object_id, is_enabled, frequency, every_n, at_time, week_days, run_kind,
      retry_count, retry_delay_minutes, owner_user_id, owner_username, next_run_at, updated_by_user_id, updated_by_username)
  VALUES (p_source_object_id, p_enabled, v_freq, CASE WHEN v_freq IN ('EVERY_MINUTES', 'EVERY_HOURS') THEN v_n END,
      v_time, v_days, v_kind, p_retry_count, p_retry_delay_minutes,
      CASE WHEN p_enabled THEN v_actor END, CASE WHEN p_enabled THEN v_username END, v_next, v_actor, v_username)
  ON CONFLICT (source_object_id) DO UPDATE
     SET is_enabled = EXCLUDED.is_enabled, frequency = EXCLUDED.frequency, every_n = EXCLUDED.every_n,
         at_time = EXCLUDED.at_time, week_days = EXCLUDED.week_days, run_kind = EXCLUDED.run_kind,
         retry_count = EXCLUDED.retry_count, retry_delay_minutes = EXCLUDED.retry_delay_minutes,
         owner_user_id = CASE WHEN EXCLUDED.is_enabled THEN EXCLUDED.owner_user_id ELSE x.owner_user_id END,
         owner_username = CASE WHEN EXCLUDED.is_enabled THEN EXCLUDED.owner_username ELSE x.owner_username END,
         next_run_at = EXCLUDED.next_run_at, updated_at = clock_timestamp(),
         updated_by_user_id = EXCLUDED.updated_by_user_id, updated_by_username = EXCLUDED.updated_by_username
  RETURNING * INTO s;
  PERFORM audit.log_security_event('OBJECT_SCHEDULE_SAVED', 'SOURCE_OBJECT', p_source_object_id::text, 'UPDATE',
    'SUCCESS', jsonb_strip_nulls(jsonb_build_object('schedule_id', s.schedule_id, 'enabled', s.is_enabled,
      'frequency', s.frequency, 'every_n', s.every_n, 'at_time', to_char(s.at_time, 'HH24:MI'), 'week_days', s.week_days,
      'run_kind', s.run_kind, 'retry_count', s.retry_count, 'retry_delay_minutes', s.retry_delay_minutes,
      'next_run_at', s.next_run_at, 'actor_user_id', v_actor)));
  RETURN jsonb_build_object('schedule_id', s.schedule_id, 'source_object_id', s.source_object_id,
    'enabled', s.is_enabled, 'next_run_at', s.next_run_at);
END
$fn$;

CREATE FUNCTION app.object_schedule_list(p_token_hash text)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog', 'app', 'etl', 'meta' AS $fn$
DECLARE v jsonb;
BEGIN
  PERFORM app.integration_run_admin_session_user(p_token_hash);
  SELECT coalesce(jsonb_agg(jsonb_build_object(
      'source_object_id', o.source_object_id, 'source_code', s.code, 'source_schema', o.source_schema,
      'source_object', o.source_object, 'is_active', o.is_active,
      'ready', etl.object_schedule_ready(o.source_object_id),
      'schedule', CASE WHEN sc.schedule_id IS NULL THEN NULL ELSE jsonb_build_object(
          'schedule_id', sc.schedule_id, 'enabled', sc.is_enabled, 'frequency', sc.frequency, 'every_n', sc.every_n,
          'at_time', to_char(sc.at_time, 'HH24:MI'), 'week_days', sc.week_days, 'run_kind', sc.run_kind,
          'retry_count', sc.retry_count, 'retry_delay_minutes', sc.retry_delay_minutes, 'time_zone', sc.time_zone,
          'owner_username', sc.owner_username, 'next_run_at', sc.next_run_at, 'last_fired_at', sc.last_fired_at,
          'updated_at', sc.updated_at, 'updated_by_username', sc.updated_by_username) END,
      'requests', (SELECT coalesce(jsonb_agg(jsonb_build_object('request_id', r.request_id,
            'trigger_kind', r.trigger_kind, 'attempt', r.attempt, 'run_kind', r.run_kind, 'status', r.status,
            'result_code', r.result_code, 'requested_by', r.requested_by_username, 'requested_at', r.requested_at,
            'finished_at', r.finished_at) ORDER BY r.request_id DESC), '[]'::jsonb)
          FROM (SELECT * FROM etl.object_run_request q WHERE q.source_object_id = o.source_object_id
                 ORDER BY q.request_id DESC LIMIT 5) r),
      'events', (SELECT coalesce(jsonb_agg(jsonb_build_object('event_id', e.event_id, 'event', e.event,
            'occurred_at', e.occurred_at, 'planned_at', e.planned_at, 'request_id', e.request_id,
            'message', e.message) ORDER BY e.event_id DESC), '[]'::jsonb)
          FROM (SELECT * FROM etl.object_schedule_event q WHERE q.schedule_id = sc.schedule_id
                 ORDER BY q.event_id DESC LIMIT 5) e))
    ORDER BY s.code, o.source_schema, o.source_object), '[]'::jsonb)
    INTO v
    FROM meta.source_object o
    JOIN meta.source_system s ON s.source_system_id = o.source_system_id
    LEFT JOIN etl.object_schedule sc ON sc.source_object_id = o.source_object_id
   WHERE o.is_active OR sc.schedule_id IS NOT NULL;
  RETURN jsonb_build_object('time_zone', 'Asia/Baku', 'now', clock_timestamp(), 'objects', v);
END
$fn$;

-- ------------------------------------------------------------------ dispatcher (dwh_etl)

-- Due schedules become requests (P4); a busy object is skipped and recorded (P5); after a pause only one run is
-- made, the next run is counted from now (P6); a failed SCHEDULED / RETRY request is repeated up to retry_count
-- times after retry_delay_minutes, unless a newer request of the object exists (P7). Returns created requests.
CREATE FUNCTION etl.object_schedule_fire(p_worker_name text)
RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog', 'etl', 'meta', 'audit' AS $fn$
DECLARE
  v_worker text := btrim(coalesce(p_worker_name, ''));
  v_now timestamptz := clock_timestamp();
  v_created integer := 0;
  v_id bigint;
  sc etl.object_schedule;
  f record;
BEGIN
  IF v_worker = '' OR char_length(v_worker) > 255 THEN RAISE EXCEPTION 'Worker name is required'; END IF;
  IF NOT pg_try_advisory_xact_lock(hashtext('dwh.object_schedule_fire')) THEN RETURN 0; END IF;
  PERFORM set_config('dwh.security_source', 'SCHEDULER', true);
  FOR sc IN SELECT * FROM etl.object_schedule x WHERE x.is_enabled AND x.next_run_at <= v_now
             ORDER BY x.next_run_at, x.schedule_id FOR UPDATE LOOP
    v_id := NULL;
    IF NOT etl.object_schedule_ready(sc.source_object_id) THEN
      INSERT INTO etl.object_schedule_event(schedule_id, source_object_id, event, planned_at, message)
      VALUES (sc.schedule_id, sc.source_object_id, 'SKIPPED_NOT_READY', sc.next_run_at,
              'Object or source is not active, or the configuration is not READY');
    ELSE
      BEGIN
        INSERT INTO etl.object_run_request(source_object_id, run_kind, requested_by_user_id, requested_by_username,
                                           trigger_kind, schedule_id, attempt)
        VALUES (sc.source_object_id, sc.run_kind, sc.owner_user_id, sc.owner_username, 'SCHEDULED', sc.schedule_id, 1)
        RETURNING request_id INTO v_id;
      EXCEPTION WHEN unique_violation THEN
        v_id := NULL;
      END;
      IF v_id IS NULL THEN
        INSERT INTO etl.object_schedule_event(schedule_id, source_object_id, event, planned_at, message)
        VALUES (sc.schedule_id, sc.source_object_id, 'SKIPPED_BUSY', sc.next_run_at, 'The object already has an active run request');
      ELSE
        INSERT INTO etl.object_schedule_event(schedule_id, source_object_id, event, planned_at, request_id)
        VALUES (sc.schedule_id, sc.source_object_id, 'QUEUED', sc.next_run_at, v_id);
        PERFORM audit.log_security_event('OBJECT_RUN_REQUEST_CREATED', 'SOURCE_OBJECT', sc.source_object_id::text,
          'CREATE', 'SUCCESS', jsonb_build_object('request_id', v_id, 'run_kind', sc.run_kind, 'trigger_kind', 'SCHEDULED',
            'schedule_id', sc.schedule_id, 'planned_at', sc.next_run_at, 'owner', sc.owner_username, 'worker', v_worker));
        v_created := v_created + 1;
      END IF;
    END IF;
    UPDATE etl.object_schedule x
       SET last_fired_at = v_now,
           next_run_at = etl.object_schedule_next(x.frequency, x.every_n, x.at_time, x.week_days, v_now)
     WHERE x.schedule_id = sc.schedule_id;
  END LOOP;
  FOR f IN SELECT r.request_id, r.source_object_id, r.run_kind, r.attempt, x.schedule_id, x.owner_user_id,
                  x.owner_username
             FROM etl.object_run_request r JOIN etl.object_schedule x ON x.schedule_id = r.schedule_id
            WHERE r.status = 'FAILED' AND r.trigger_kind IN ('SCHEDULED', 'RETRY') AND x.is_enabled
              AND r.attempt <= x.retry_count
              AND r.finished_at + make_interval(mins => x.retry_delay_minutes) <= v_now
              AND r.finished_at > v_now - interval '1 day'
              AND NOT EXISTS (SELECT 1 FROM etl.object_run_request c WHERE c.retry_of_request_id = r.request_id)
              AND NOT EXISTS (SELECT 1 FROM etl.object_run_request n
                               WHERE n.source_object_id = r.source_object_id AND n.request_id > r.request_id)
            ORDER BY r.request_id LOOP
    CONTINUE WHEN NOT etl.object_schedule_ready(f.source_object_id);
    v_id := NULL;
    BEGIN
      INSERT INTO etl.object_run_request(source_object_id, run_kind, requested_by_user_id, requested_by_username,
                                         trigger_kind, schedule_id, attempt, retry_of_request_id)
      VALUES (f.source_object_id, f.run_kind, f.owner_user_id, f.owner_username, 'RETRY', f.schedule_id,
              f.attempt + 1, f.request_id)
      RETURNING request_id INTO v_id;
    EXCEPTION WHEN unique_violation THEN
      v_id := NULL;
    END;
    CONTINUE WHEN v_id IS NULL;
    INSERT INTO etl.object_schedule_event(schedule_id, source_object_id, event, request_id, message)
    VALUES (f.schedule_id, f.source_object_id, 'RETRY_QUEUED', v_id,
            'Repeat ' || f.attempt || ' of failed request ' || f.request_id);
    PERFORM audit.log_security_event('OBJECT_RUN_REQUEST_CREATED', 'SOURCE_OBJECT', f.source_object_id::text,
      'CREATE', 'SUCCESS', jsonb_build_object('request_id', v_id, 'run_kind', f.run_kind, 'trigger_kind', 'RETRY',
        'schedule_id', f.schedule_id, 'attempt', f.attempt + 1, 'retry_of_request_id', f.request_id,
        'owner', f.owner_username, 'worker', v_worker));
    v_created := v_created + 1;
  END LOOP;
  RETURN v_created;
END
$fn$;

-- ------------------------------------------------------------------ privileges

REVOKE ALL ON etl.object_schedule, etl.object_schedule_event FROM PUBLIC;
REVOKE ALL ON FUNCTION etl.object_schedule_next(text, integer, time, smallint[], timestamptz),
  etl.object_schedule_ready(bigint),
  app.object_schedule_save(text, bigint, boolean, text, integer, text, integer[], text, integer, integer),
  app.object_schedule_list(text), etl.object_schedule_fire(text) FROM PUBLIC;

-- Default privileges of schemas etl/app (rule since 005): remove every grant on the new objects except the owner's.
DO $acl$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT DISTINCT CASE c.relkind WHEN 'S' THEN 'SEQUENCE ' ELSE 'TABLE ' END || c.oid::regclass::text AS obj, x.grantee
      FROM pg_class c, aclexplode(c.relacl) x
     WHERE (c.oid IN ('etl.object_schedule'::regclass, 'etl.object_schedule_event'::regclass)
            OR c.oid IN (SELECT d.objid FROM pg_depend d
                          WHERE d.refobjid IN ('etl.object_schedule'::regclass, 'etl.object_schedule_event'::regclass)
                            AND d.classid = 'pg_class'::regclass AND d.deptype IN ('a', 'i')))
       AND x.grantee <> c.relowner
    UNION
    SELECT 'FUNCTION ' || p.oid::regprocedure::text, x.grantee
      FROM pg_proc p, aclexplode(p.proacl) x
     WHERE p.oid IN ('etl.object_schedule_next(text, integer, time, smallint[], timestamptz)'::regprocedure,
                     'etl.object_schedule_ready(bigint)'::regprocedure,
                     'app.object_schedule_save(text, bigint, boolean, text, integer, text, integer[], text, integer, integer)'::regprocedure,
                     'app.object_schedule_list(text)'::regprocedure,
                     'etl.object_schedule_fire(text)'::regprocedure)
       AND x.grantee <> p.proowner
  LOOP
    EXECUTE format('REVOKE ALL ON %s FROM %s', r.obj,
                   CASE WHEN r.grantee = 0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    RAISE NOTICE 'DEFAULT_GRANT_REVOKED % FROM %', r.obj,
                 CASE WHEN r.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(r.grantee) END;
  END LOOP;
END
$acl$;

GRANT EXECUTE ON FUNCTION
  app.object_schedule_save(text, bigint, boolean, text, integer, text, integer[], text, integer, integer),
  app.object_schedule_list(text)
TO dwh_app;
GRANT EXECUTE ON FUNCTION etl.object_schedule_fire(text) TO dwh_etl;

SELECT 'MIGRATION_032_DONE' AS result;
