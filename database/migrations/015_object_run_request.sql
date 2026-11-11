-- I08 (step 3): run requests of source objects from the UI -- package 015
-- (plan docs/DWH_I08_MANUAL_RUN_PLAN_APPROVED_20261111.md, decisions R1 = A, R2 = A, R3 = A).
--
-- R1 = A  A queue of its own for objects: etl.object_run_request. The CRM queue etl.run_request and its
--         functions are not changed (step 2: the source batch is mandatory there and the task list closed).
-- R2 = A  A request is created through app.integration_run_admin_session_user, the same session and
--         permission check as the CRM run requests ("Run Control").
-- R3 = A  Kinds: RUN (STG + ODS by the strategy of the version in force), CONTINUE_ODS (only the ODS step,
--         allowed when the last STG run succeeded and the ODS step after it failed), FULL_RERUN (STG
--         reading the whole object once, then ODS).
-- Requests are executed by the dispatcher (role dwh_etl): claim -> start -> finish. As in the CRM queue,
-- the claim takes a session advisory lock per request; a CLAIMED/RUNNING request whose lock is free belongs
-- to a lost dispatcher and is closed FAILED by the next claim.
-- etl.v_object_load_status gives dwh_app (UI tab "Objects") the state of every object: configuration in
-- force, last STG and ODS runs, last error, active and last request. dwh_app does not read the run tables.
-- Nothing else changes: functions of 008-014, the CRM queue and their privileges stay as they are.
-- Requires 001-014 and app.integration_run_admin_session_user(text). Run as the owner of schemas etl, app:
--   psql -1 -v ON_ERROR_STOP=1 -f 015_object_run_request.sql
DO $pre$
BEGIN
  SET LOCAL search_path = pg_catalog;
  IF to_regprocedure('etl.object_ods_run_finish(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text, text, jsonb)') IS NULL THEN
    RAISE EXCEPTION 'STOP: 014 must be applied first';
  END IF;
  IF to_regprocedure('app.integration_run_admin_session_user(text)') IS NULL THEN
    RAISE EXCEPTION 'STOP: app.integration_run_admin_session_user(text) not found';
  END IF;
  IF to_regclass('etl.object_run_request') IS NOT NULL THEN
    RAISE EXCEPTION 'STOP: 015 is already applied';
  END IF;
END
$pre$;

CREATE TABLE etl.object_run_request (
  request_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source_object_id bigint NOT NULL REFERENCES meta.source_object(source_object_id) ON DELETE RESTRICT,
  run_kind text NOT NULL CHECK (run_kind IN ('RUN', 'CONTINUE_ODS', 'FULL_RERUN')),
  status text NOT NULL DEFAULT 'QUEUED'
    CHECK (status IN ('QUEUED', 'CLAIMED', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED')),
  requested_by_user_id bigint NOT NULL,
  requested_by_username text NOT NULL CHECK (char_length(requested_by_username) BETWEEN 1 AND 255),
  requested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  claimed_by text CHECK (char_length(claimed_by) BETWEEN 1 AND 255),
  claimed_at timestamptz,
  started_at timestamptz,
  finished_at timestamptz,
  stg_run_id bigint REFERENCES etl.object_run(run_id) ON DELETE RESTRICT,
  ods_run_id bigint REFERENCES etl.object_ods_run(ods_run_id) ON DELETE RESTRICT,
  result_code text CHECK (result_code ~ '^[A-Z][A-Z0-9_]{0,59}$'),
  message text CHECK (char_length(message) <= 4000),
  CONSTRAINT object_run_request_finished CHECK (
    (status IN ('SUCCESS', 'FAILED', 'CANCELLED')) = (finished_at IS NOT NULL)),
  CONSTRAINT object_run_request_claimed CHECK (
    status IN ('QUEUED', 'CANCELLED') OR (claimed_by IS NOT NULL AND claimed_at IS NOT NULL)
    OR (status = 'FAILED' AND claimed_at IS NULL))
);
-- One active request per object.
CREATE UNIQUE INDEX object_run_request_active ON etl.object_run_request (source_object_id)
  WHERE status IN ('QUEUED', 'CLAIMED', 'RUNNING');
CREATE INDEX object_run_request_queue ON etl.object_run_request (status, requested_at, request_id);
CREATE INDEX object_run_request_object ON etl.object_run_request (source_object_id, request_id DESC);

-- ------------------------------------------------------------------ UI (dwh_app)

CREATE FUNCTION app.integration_object_run_request_create(p_token_hash text, p_source_object_id bigint, p_run_kind text)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, etl, meta, audit AS $fn$
DECLARE
  v_actor bigint;
  v_username text;
  v_kind text := upper(btrim(coalesce(p_run_kind, '')));
  o meta.source_object;
  v_stg etl.object_run;
  v_ods etl.object_ods_run;
  v_id bigint;
BEGIN
  v_actor := app.integration_run_admin_session_user(p_token_hash);
  v_username := coalesce(nullif(current_setting('dwh.actor_username', true), ''), 'user ' || v_actor);
  IF v_kind NOT IN ('RUN', 'CONTINUE_ODS', 'FULL_RERUN') THEN RAISE EXCEPTION 'Unsupported run kind'; END IF;
  SELECT * INTO o FROM meta.source_object x WHERE x.source_object_id = p_source_object_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Source object not found'; END IF;
  IF NOT o.is_active THEN RAISE EXCEPTION 'Source object is not active'; END IF;
  IF meta.source_is_legacy(o.source_system_id) THEN
    RAISE EXCEPTION 'Legacy CRM sources are run by their own ETL';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM meta.source_system s WHERE s.source_system_id = o.source_system_id AND s.is_active) THEN
    RAISE EXCEPTION 'Source system is not active';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM meta.object_config_current c WHERE c.source_object_id = o.source_object_id AND c.status = 'READY') THEN
    RAISE EXCEPTION 'Object has no published READY configuration';
  END IF;
  IF v_kind = 'CONTINUE_ODS' THEN
    IF NOT EXISTS (SELECT 1 FROM meta.object_ods_current c WHERE c.source_object_id = o.source_object_id AND c.status = 'READY') THEN
      RAISE EXCEPTION 'Object has no published READY ODS configuration';
    END IF;
    SELECT * INTO v_stg FROM etl.object_run r WHERE r.source_object_id = o.source_object_id ORDER BY r.run_id DESC LIMIT 1;
    SELECT * INTO v_ods FROM etl.object_ods_run r WHERE r.source_object_id = o.source_object_id ORDER BY r.ods_run_id DESC LIMIT 1;
    IF v_stg.run_id IS NULL OR v_stg.status <> 'SUCCESS' OR v_ods.ods_run_id IS NULL OR v_ods.status <> 'FAILED'
       OR v_ods.started_at < v_stg.finished_at THEN
      RAISE EXCEPTION 'Continue is possible only after a failed ODS step that follows a successful STG run';
    END IF;
  END IF;
  IF EXISTS (SELECT 1 FROM etl.object_run_request r WHERE r.source_object_id = o.source_object_id
              AND r.status IN ('QUEUED', 'CLAIMED', 'RUNNING')) THEN
    RAISE EXCEPTION 'Object already has an active run request';
  END IF;
  INSERT INTO etl.object_run_request(source_object_id, run_kind, requested_by_user_id, requested_by_username)
  VALUES (o.source_object_id, v_kind, v_actor, left(v_username, 255))
  RETURNING request_id INTO v_id;
  PERFORM audit.log_security_event('OBJECT_RUN_REQUEST_CREATED', 'SOURCE_OBJECT', o.source_object_id::text, 'CREATE',
    'SUCCESS', jsonb_build_object('request_id', v_id, 'run_kind', v_kind, 'actor_user_id', v_actor));
  RETURN v_id;
END;
$fn$;

CREATE FUNCTION app.integration_object_run_request_cancel(p_token_hash text, p_request_id bigint)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, etl, audit AS $fn$
DECLARE
  v_actor bigint;
  r etl.object_run_request;
BEGIN
  v_actor := app.integration_run_admin_session_user(p_token_hash);
  UPDATE etl.object_run_request x
     SET status = 'CANCELLED', finished_at = clock_timestamp(), result_code = 'CANCELLED',
         message = 'Cancelled before execution'
   WHERE x.request_id = p_request_id AND x.status = 'QUEUED'
  RETURNING * INTO r;
  IF NOT FOUND THEN
    IF NOT EXISTS (SELECT 1 FROM etl.object_run_request x WHERE x.request_id = p_request_id) THEN
      RAISE EXCEPTION 'Run request does not exist';
    END IF;
    RAISE EXCEPTION 'Only QUEUED run requests can be cancelled';
  END IF;
  PERFORM audit.log_security_event('OBJECT_RUN_REQUEST_CANCELLED', 'SOURCE_OBJECT', r.source_object_id::text, 'CANCEL',
    'SUCCESS', jsonb_build_object('request_id', p_request_id, 'actor_user_id', v_actor));
  RETURN true;
END;
$fn$;

-- ------------------------------------------------------------------ dispatcher (dwh_etl)

CREATE FUNCTION etl.object_run_request_claim(p_worker_name text)
RETURNS TABLE(request_id bigint, source_object_id bigint, run_kind text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, etl AS $fn$
DECLARE
  v_worker text := btrim(coalesce(p_worker_name, ''));
  v_key integer := hashtext('dwh.object_run_request');
  a record;
  c etl.object_run_request;
BEGIN
  IF v_worker = '' OR char_length(v_worker) > 255 THEN RAISE EXCEPTION 'Worker name is required'; END IF;
  -- 1. Requests of a lost dispatcher: active, but nobody holds their session lock. A lock held by this
  --    session is skipped (advisory locks are re-entrant: the try would succeed on our own request).
  FOR a IN SELECT x.request_id, x.status, x.claimed_by FROM etl.object_run_request x
            WHERE x.status IN ('CLAIMED', 'RUNNING') ORDER BY x.request_id LOOP
    CONTINUE WHEN EXISTS (SELECT 1 FROM pg_locks l WHERE l.locktype = 'advisory' AND l.pid = pg_backend_pid()
                           AND l.classid = v_key::oid AND l.objid = a.request_id::integer::oid AND l.objsubid = 2);
    IF pg_try_advisory_lock(v_key, a.request_id::integer) THEN
      UPDATE etl.object_run_request x
         SET status = 'FAILED', finished_at = clock_timestamp(), result_code = 'DISPATCHER_LOST',
             message = left('Recovered abandoned ' || a.status || ' request: dispatcher session ended; previous worker='
                            || coalesce(a.claimed_by, '<unknown>') || '; recovered by=' || v_worker, 4000)
       WHERE x.request_id = a.request_id AND x.status IN ('CLAIMED', 'RUNNING');
      PERFORM pg_advisory_unlock(v_key, a.request_id::integer);
    END IF;
  END LOOP;
  -- 2. The oldest queued request.
  SELECT * INTO c FROM etl.object_run_request x WHERE x.status = 'QUEUED'
   ORDER BY x.requested_at, x.request_id FOR UPDATE SKIP LOCKED LIMIT 1;
  IF NOT FOUND THEN RETURN; END IF;
  -- 3. Session ownership before CLAIMED (released by finish or by the end of the session).
  IF NOT pg_try_advisory_lock(v_key, c.request_id::integer) THEN
    RAISE EXCEPTION 'Queued run request lock is unexpectedly held';
  END IF;
  UPDATE etl.object_run_request x SET status = 'CLAIMED', claimed_by = v_worker, claimed_at = clock_timestamp()
   WHERE x.request_id = c.request_id AND x.status = 'QUEUED';
  RETURN QUERY SELECT c.request_id, c.source_object_id, c.run_kind;
END;
$fn$;

CREATE FUNCTION etl.object_run_request_start(p_request_id bigint, p_worker_name text)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, etl AS $fn$
BEGIN
  UPDATE etl.object_run_request x SET status = 'RUNNING', started_at = clock_timestamp()
   WHERE x.request_id = p_request_id AND x.status = 'CLAIMED' AND x.claimed_by = btrim(coalesce(p_worker_name, ''));
  IF NOT FOUND THEN RAISE EXCEPTION 'Run request cannot be started'; END IF;
  RETURN true;
END;
$fn$;

-- p_ok: the request reached its goal (every step it ran succeeded). The runs must belong to the object.
CREATE FUNCTION etl.object_run_request_finish(p_request_id bigint, p_ok boolean, p_stg_run_id bigint,
  p_ods_run_id bigint, p_result_code text, p_message text)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, etl AS $fn$
DECLARE
  r etl.object_run_request;
  v_status text := CASE WHEN p_ok THEN 'SUCCESS' ELSE 'FAILED' END;
BEGIN
  IF p_ok IS NULL OR (p_result_code IS NOT NULL AND p_result_code !~ '^[A-Z][A-Z0-9_]{0,59}$')
     OR (NOT p_ok AND p_result_code IS NULL) THEN
    RAISE EXCEPTION 'Invalid request result';
  END IF;
  SELECT * INTO r FROM etl.object_run_request x WHERE x.request_id = p_request_id FOR UPDATE;
  IF NOT FOUND OR r.status <> 'RUNNING' THEN RAISE EXCEPTION 'Running request does not exist'; END IF;
  IF p_stg_run_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM etl.object_run x
       WHERE x.run_id = p_stg_run_id AND x.source_object_id = r.source_object_id AND x.started_at >= r.started_at) THEN
    RAISE EXCEPTION 'STG run does not belong to this request';
  END IF;
  IF p_ods_run_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM etl.object_ods_run x
       WHERE x.ods_run_id = p_ods_run_id AND x.source_object_id = r.source_object_id AND x.started_at >= r.started_at) THEN
    RAISE EXCEPTION 'ODS run does not belong to this request';
  END IF;
  UPDATE etl.object_run_request x
     SET status = v_status, finished_at = clock_timestamp(), stg_run_id = p_stg_run_id, ods_run_id = p_ods_run_id,
         result_code = p_result_code, message = left(nullif(btrim(coalesce(p_message, '')), ''), 4000)
   WHERE x.request_id = p_request_id;
  PERFORM pg_advisory_unlock(hashtext('dwh.object_run_request'), p_request_id::integer);
  RETURN v_status;
END;
$fn$;

-- ------------------------------------------------------------------ state of objects (UI, read-only)

CREATE VIEW etl.v_object_load_status AS
SELECT o.source_object_id, s.code AS source_system_code, o.source_schema, o.source_object, o.is_active,
  (EXISTS (SELECT 1 FROM meta.column_contract cc WHERE cc.source_system_code = s.code)
   OR EXISTS (SELECT 1 FROM meta.mapping_rule mr WHERE mr.source_system_code = s.code)) AS is_legacy,
  cv.version_no AS load_version_no, cc.status AS load_status, cv.load_strategy,
  cv.target_schema AS stg_schema, cv.target_table AS stg_table,
  ov.version_no AS ods_version_no, oc.status AS ods_status, ov.target_table AS ods_table,
  lr.run_id AS stg_run_id, lr.batch_run_id AS stg_batch_run_id, lr.status AS stg_run_status,
  lr.started_at AS stg_started_at, lr.finished_at AS stg_finished_at, lr.rows_read AS stg_rows_read,
  lr.rows_inserted AS stg_rows_inserted, lr.rows_updated AS stg_rows_updated, lr.error_code AS stg_error_code,
  (SELECT left(e.error_message, 2000) FROM etl.error_log e WHERE e.batch_run_id = lr.batch_run_id
    ORDER BY e.error_id DESC LIMIT 1) AS stg_error_message,
  lo.ods_run_id, lo.batch_run_id AS ods_batch_run_id, lo.status AS ods_run_status, lo.stg_run_id AS ods_from_stg_run_id,
  lo.started_at AS ods_started_at, lo.finished_at AS ods_finished_at, lo.rows_read AS ods_rows_read,
  lo.rows_inserted AS ods_rows_inserted, lo.rows_updated AS ods_rows_updated, lo.error_code AS ods_error_code,
  (SELECT left(e.error_message, 2000) FROM etl.error_log e WHERE e.batch_run_id = lo.batch_run_id
    ORDER BY e.error_id DESC LIMIT 1) AS ods_error_message,
  coalesce(lr.status = 'SUCCESS' AND lo.status = 'FAILED' AND lo.started_at >= lr.finished_at
   AND oc.status = 'READY', false) AS can_continue_ods,
  rq.request_id AS last_request_id, rq.run_kind AS last_request_kind, rq.status AS last_request_status,
  rq.requested_by_username AS last_request_by, rq.requested_at AS last_request_at,
  rq.finished_at AS last_request_finished_at, rq.result_code AS last_request_result, rq.message AS last_request_message,
  coalesce(rq.status IN ('QUEUED', 'CLAIMED', 'RUNNING'), false) AS has_active_request
FROM meta.source_object o
JOIN meta.source_system s ON s.source_system_id = o.source_system_id
LEFT JOIN meta.object_config_current cc ON cc.source_object_id = o.source_object_id
LEFT JOIN meta.object_config_version cv ON cv.version_id = cc.version_id
LEFT JOIN meta.object_ods_current oc ON oc.source_object_id = o.source_object_id
LEFT JOIN meta.object_ods_version ov ON ov.ods_version_id = oc.ods_version_id
LEFT JOIN LATERAL (SELECT * FROM etl.object_run r WHERE r.source_object_id = o.source_object_id
                    ORDER BY r.run_id DESC LIMIT 1) lr ON true
LEFT JOIN LATERAL (SELECT * FROM etl.object_ods_run r WHERE r.source_object_id = o.source_object_id
                    ORDER BY r.ods_run_id DESC LIMIT 1) lo ON true
LEFT JOIN LATERAL (SELECT * FROM etl.object_run_request r WHERE r.source_object_id = o.source_object_id
                    ORDER BY r.request_id DESC LIMIT 1) rq ON true;

-- ------------------------------------------------------------------ privileges

REVOKE ALL ON etl.object_run_request, etl.v_object_load_status FROM PUBLIC;
REVOKE ALL ON FUNCTION
  app.integration_object_run_request_create(text, bigint, text),
  app.integration_object_run_request_cancel(text, bigint),
  etl.object_run_request_claim(text),
  etl.object_run_request_start(bigint, text),
  etl.object_run_request_finish(bigint, boolean, bigint, bigint, text, text)
FROM PUBLIC;

-- Default privileges of schemas etl/app (rule since 005): remove every grant on the new objects except
-- the owner's and report it, then grant only what is intended.
DO $acl$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT DISTINCT CASE c.relkind WHEN 'S' THEN 'SEQUENCE ' ELSE 'TABLE ' END || c.oid::regclass::text AS obj, x.grantee
      FROM pg_class c, aclexplode(c.relacl) x
     WHERE (c.oid IN ('etl.object_run_request'::regclass, 'etl.v_object_load_status'::regclass)
            OR c.oid IN (SELECT d.objid FROM pg_depend d
                          WHERE d.refobjid = 'etl.object_run_request'::regclass
                            AND d.classid = 'pg_class'::regclass AND d.deptype IN ('a', 'i')))
       AND x.grantee <> c.relowner
    UNION
    SELECT 'FUNCTION ' || p.oid::regprocedure::text, x.grantee
      FROM pg_proc p, aclexplode(p.proacl) x
     WHERE p.oid IN ('app.integration_object_run_request_create(text, bigint, text)'::regprocedure,
                     'app.integration_object_run_request_cancel(text, bigint)'::regprocedure,
                     'etl.object_run_request_claim(text)'::regprocedure,
                     'etl.object_run_request_start(bigint, text)'::regprocedure,
                     'etl.object_run_request_finish(bigint, boolean, bigint, bigint, text, text)'::regprocedure)
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
  app.integration_object_run_request_create(text, bigint, text),
  app.integration_object_run_request_cancel(text, bigint)
TO dwh_app;
GRANT SELECT ON etl.v_object_load_status TO dwh_app;
GRANT EXECUTE ON FUNCTION
  etl.object_run_request_claim(text),
  etl.object_run_request_start(bigint, text),
  etl.object_run_request_finish(bigint, boolean, bigint, bigint, text, text)
TO dwh_etl;

SELECT 'MIGRATION_015_DONE' AS result;
