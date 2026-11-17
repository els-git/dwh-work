-- I19 rollback of 019: back to the DM builder of 018 (tables of marts created by packages).
-- Refuses and changes nothing if 019 was used: a deleted mart, a version being built or failed, or a PUBLISH
-- request (their history would lose meaning). Restores the 018 text of etl.dm_mart_run_request_claim,
-- etl.dm_mart_run_begin, etl.dm_mart_run_finish and etl.v_dm_mart_status (grants as in 018).
-- Run as the owner:   psql -v ON_ERROR_STOP=1 -f 019_dm_builder_ui_ddl.down.sql
BEGIN;
LOCK TABLE meta.dm_mart, meta.dm_mart_version, etl.dm_mart_run_request IN ACCESS EXCLUSIVE MODE;
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM meta.dm_mart WHERE deleted_at IS NOT NULL)
     OR EXISTS (SELECT 1 FROM meta.dm_mart_version WHERE build_status <> 'NONE')
     OR EXISTS (SELECT 1 FROM etl.dm_mart_run_request WHERE trigger_kind = 'PUBLISH') THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: marts were built or deleted from the UI (019 in use)';
  END IF;
END
$do$;

CREATE OR REPLACE FUNCTION etl.dm_mart_run_request_claim(p_worker_name text)
RETURNS TABLE(request_id bigint, mart_id bigint, version_id bigint, trigger_kind text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, etl, meta AS $fn$
DECLARE
  v_worker text := btrim(coalesce(p_worker_name, ''));
  v_key integer := hashtext('dwh.dm_mart_run_request');
  a record;
  c etl.dm_mart_run_request;
  v_version bigint;
BEGIN
  IF v_worker = '' OR char_length(v_worker) > 255 THEN RAISE EXCEPTION 'Worker name is required'; END IF;
  FOR a IN SELECT x.request_id, x.status, x.claimed_by, x.run_id FROM etl.dm_mart_run_request x
            WHERE x.status IN ('CLAIMED', 'RUNNING') ORDER BY x.request_id LOOP
    CONTINUE WHEN EXISTS (SELECT 1 FROM pg_locks l WHERE l.locktype = 'advisory' AND l.pid = pg_backend_pid()
                           AND l.classid = v_key::oid AND l.objid = a.request_id::integer::oid AND l.objsubid = 2);
    IF pg_try_advisory_lock(v_key, a.request_id::integer) THEN
      UPDATE etl.dm_mart_run x SET status = 'FAILED', finished_at = clock_timestamp(), error_code = 'DISPATCHER_LOST',
             error_message = 'Dispatcher session ended during the run'
       WHERE x.request_id = a.request_id AND x.status = 'RUNNING';
      UPDATE etl.dm_mart_run_request x
         SET status = 'FAILED', finished_at = clock_timestamp(), result_code = 'DISPATCHER_LOST',
             run_id = (SELECT r.run_id FROM etl.dm_mart_run r WHERE r.request_id = a.request_id),
             message = left('Recovered abandoned ' || a.status || ' request: dispatcher session ended; previous worker='
                            || coalesce(a.claimed_by, '<unknown>') || '; recovered by=' || v_worker, 4000)
       WHERE x.request_id = a.request_id AND x.status IN ('CLAIMED', 'RUNNING');
      PERFORM pg_advisory_unlock(v_key, a.request_id::integer);
    END IF;
  END LOOP;
  SELECT * INTO c FROM etl.dm_mart_run_request x
   WHERE x.status = 'QUEUED'
     AND NOT EXISTS (SELECT 1 FROM etl.dm_mart_run_request y WHERE y.mart_id = x.mart_id AND y.status IN ('CLAIMED', 'RUNNING'))
   ORDER BY x.requested_at, x.request_id FOR UPDATE SKIP LOCKED LIMIT 1;
  IF NOT FOUND THEN RETURN; END IF;
  SELECT v.version_id INTO v_version FROM meta.dm_mart_version v JOIN meta.dm_mart m ON m.mart_id = v.mart_id
   WHERE v.mart_id = c.mart_id AND v.status = 'PUBLISHED' AND m.is_active;
  IF v_version IS NULL THEN
    UPDATE etl.dm_mart_run_request x SET status = 'FAILED', finished_at = clock_timestamp(), result_code = 'MART_NOT_RUNNABLE',
           message = 'Mart is inactive or has no published version'
     WHERE x.request_id = c.request_id;
    RETURN;
  END IF;
  IF NOT pg_try_advisory_lock(v_key, c.request_id::integer) THEN
    RAISE EXCEPTION 'Queued run request lock is unexpectedly held';
  END IF;
  UPDATE etl.dm_mart_run_request x SET status = 'CLAIMED', claimed_by = v_worker, claimed_at = clock_timestamp()
   WHERE x.request_id = c.request_id AND x.status = 'QUEUED';
  RETURN QUERY SELECT c.request_id, c.mart_id, v_version, c.trigger_kind;
END;
$fn$;


CREATE OR REPLACE FUNCTION etl.dm_mart_run_begin(p_request_id bigint, p_worker_name text, p_version_id bigint)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, etl, meta AS $fn$
DECLARE
  r etl.dm_mart_run_request;
  v_run bigint;
BEGIN
  SELECT * INTO r FROM etl.dm_mart_run_request x WHERE x.request_id = p_request_id FOR UPDATE;
  IF NOT FOUND OR r.status <> 'CLAIMED' OR r.claimed_by IS DISTINCT FROM btrim(coalesce(p_worker_name, '')) THEN
    RAISE EXCEPTION 'Run request cannot be started';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM meta.dm_mart_version v WHERE v.version_id = p_version_id AND v.mart_id = r.mart_id
                  AND v.status = 'PUBLISHED') THEN
    RAISE EXCEPTION 'Version is not the published version of the mart';
  END IF;
  UPDATE etl.dm_mart_run_request x SET status = 'RUNNING', started_at = clock_timestamp() WHERE x.request_id = p_request_id;
  INSERT INTO etl.dm_mart_run (mart_id, version_id, request_id) VALUES (r.mart_id, p_version_id, p_request_id)
  RETURNING run_id INTO v_run;
  RETURN v_run;
END;
$fn$;


CREATE OR REPLACE FUNCTION etl.dm_mart_run_finish(p_run_id bigint, p_ok boolean, p_rows_deleted bigint, p_rows_inserted bigint,
  p_error_code text, p_error_message text, p_diagnostic jsonb)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, etl AS $fn$
DECLARE
  r etl.dm_mart_run;
  v_status text := CASE WHEN p_ok THEN 'SUCCESS' ELSE 'FAILED' END;
BEGIN
  IF p_ok IS NULL OR (NOT p_ok AND p_error_code IS NULL) OR (p_ok AND p_error_code IS NOT NULL)
     OR (p_error_code IS NOT NULL AND p_error_code !~ '^[A-Z0-9][A-Z0-9_]{0,59}$') THEN
    RAISE EXCEPTION 'Invalid run result';
  END IF;
  SELECT * INTO r FROM etl.dm_mart_run x WHERE x.run_id = p_run_id FOR UPDATE;
  IF NOT FOUND OR r.status <> 'RUNNING' THEN RAISE EXCEPTION 'Running mart run does not exist'; END IF;
  UPDATE etl.dm_mart_run x SET status = v_status, finished_at = clock_timestamp(), rows_deleted = p_rows_deleted,
         rows_inserted = p_rows_inserted, error_code = p_error_code,
         error_message = left(nullif(btrim(coalesce(p_error_message, '')), ''), 4000), diagnostic = p_diagnostic
   WHERE x.run_id = p_run_id;
  UPDATE etl.dm_mart_run_request x SET status = v_status, finished_at = clock_timestamp(), run_id = p_run_id,
         result_code = p_error_code, message = left(nullif(btrim(coalesce(p_error_message, '')), ''), 4000)
   WHERE x.request_id = r.request_id AND x.status = 'RUNNING';
  PERFORM pg_advisory_unlock(hashtext('dwh.dm_mart_run_request'), r.request_id::integer);
  RETURN v_status;
END;
$fn$;


DROP VIEW etl.v_dm_mart_status;

DROP FUNCTION app.mart_builder_delete(text, bigint, boolean);
DROP FUNCTION app.mart_builder_publish(text, bigint, boolean);
DROP FUNCTION app.mart_builder_preview(text, bigint);
DROP FUNCTION meta.mart_builder_plan(bigint);
DROP FUNCTION meta.mart_builder_publish_now(bigint, bigint);
DROP FUNCTION meta.mart_builder_build_failed(bigint, text);
DROP FUNCTION meta.mart_builder_impact_empty(jsonb);
DROP FUNCTION meta.mart_builder_impact(bigint, text[]);
DROP FUNCTION meta.mart_builder_changes(bigint);
DROP FUNCTION meta.mart_builder_ddl(bigint, text);
DROP TRIGGER dm_mart_filter_building_guard ON meta.dm_mart_filter;
DROP TRIGGER dm_mart_field_building_guard ON meta.dm_mart_field;
DROP TRIGGER dm_mart_source_building_guard ON meta.dm_mart_source;
DROP FUNCTION meta.mart_builder_building_guard();
DROP TRIGGER dm_mart_version_deleted_guard ON meta.dm_mart_version;
DROP TRIGGER dm_mart_deleted_guard ON meta.dm_mart;
DROP FUNCTION meta.mart_builder_guard();

ALTER TABLE etl.dm_mart_run_request DROP CONSTRAINT dm_mart_run_request_trigger;
ALTER TABLE etl.dm_mart_run_request ADD CONSTRAINT dm_mart_run_request_trigger CHECK ((trigger_kind = 'MANUAL') = (requested_by_user_id IS NOT NULL)
    AND (trigger_kind = 'AFTER_ODS') = (trigger_table IS NOT NULL));
ALTER TABLE etl.dm_mart_run_request DROP CONSTRAINT dm_mart_run_request_trigger_kind_check;
ALTER TABLE etl.dm_mart_run_request ADD CONSTRAINT dm_mart_run_request_trigger_kind_check
  CHECK (trigger_kind IN ('MANUAL', 'AFTER_ODS'));
ALTER TABLE etl.dm_mart_run_request DROP COLUMN version_id;
ALTER TABLE meta.dm_mart_version DROP COLUMN build_request_id, DROP COLUMN build_message, DROP COLUMN build_status;
ALTER TABLE meta.dm_mart DROP COLUMN deleted_by_user_id, DROP COLUMN deleted_at;

-- The 018 view selects * from the request table: recreated after its columns are back to 018.
CREATE VIEW etl.v_dm_mart_status AS
SELECT m.mart_id, m.mart_code, m.title, m.description, m.required_permission_code, m.ai_enabled, m.auto_after_ods,
  m.is_active, pv.version_id AS published_version_id, pv.version_no AS published_version_no, pv.published_at,
  dv.version_id AS draft_version_id, dv.version_no AS draft_version_no, dv.ddl_status AS draft_ddl_status,
  (to_regclass(format('dm.%I', m.mart_code)) IS NOT NULL) AS table_exists,
  lr.run_id AS last_run_id, lr.status AS last_run_status, lr.started_at AS last_run_started_at,
  lr.finished_at AS last_run_finished_at, lr.rows_inserted AS last_run_rows, lr.error_code AS last_run_error_code,
  lr.error_message AS last_run_error_message, lr.diagnostic AS last_run_diagnostic,
  rq.request_id AS last_request_id, rq.trigger_kind AS last_request_trigger, rq.status AS last_request_status,
  rq.requested_by AS last_request_by, rq.requested_at AS last_request_at, rq.result_code AS last_request_result,
  coalesce(EXISTS (SELECT 1 FROM etl.dm_mart_run_request x WHERE x.mart_id = m.mart_id
                    AND x.status IN ('QUEUED', 'CLAIMED', 'RUNNING')), false) AS has_active_request
FROM meta.dm_mart m
LEFT JOIN meta.dm_mart_version pv ON pv.mart_id = m.mart_id AND pv.status = 'PUBLISHED'
LEFT JOIN meta.dm_mart_version dv ON dv.mart_id = m.mart_id AND dv.status = 'DRAFT'
LEFT JOIN LATERAL (SELECT * FROM etl.dm_mart_run r WHERE r.mart_id = m.mart_id ORDER BY r.run_id DESC LIMIT 1) lr ON true
LEFT JOIN LATERAL (SELECT * FROM etl.dm_mart_run_request r WHERE r.mart_id = m.mart_id
                    ORDER BY r.request_id DESC LIMIT 1) rq ON true;

-- Default privileges of etl apply to the recreated view: keep only the 018 grants.
DO $acl$
DECLARE
  r record;
BEGIN
  FOR r IN SELECT DISTINCT x.grantee FROM pg_class c, aclexplode(c.relacl) x
            WHERE c.oid = 'etl.v_dm_mart_status'::regclass AND x.grantee <> c.relowner LOOP
    EXECUTE format('REVOKE ALL ON etl.v_dm_mart_status FROM %s',
                   CASE WHEN r.grantee = 0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
  END LOOP;
END
$acl$;
GRANT SELECT ON etl.v_dm_mart_status TO dwh_app;

SELECT 'ROLLBACK_019_DONE' AS result;
COMMIT;
