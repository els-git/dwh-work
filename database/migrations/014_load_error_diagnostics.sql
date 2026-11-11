-- I13-08 (step 2): readable causes of load errors -- package 014
-- (plan docs/DWH_I13_08_ERROR_DIAGNOSTICS_PLAN_APPROVED_20261111.md, decision R2 = B).
--
-- Adds new variants of the two run-finish functions with two more parameters:
--   etl.object_run_finish(..10 parameters of 010.., p_bad_value text, p_diagnostic jsonb)      (STG step)
--   etl.object_ods_run_finish(..10 parameters of 012.., p_bad_value text, p_diagnostic jsonb)  (ODS step)
-- Each new variant calls the unchanged 10-parameter variant (all its checks, the run/job/batch status,
-- the etl.error_log row, the audit event and the unlock stay exactly as they are) and then writes the
-- details into that same error_log row, in the same transaction:
--   bad_value          -- the most frequent invalid value of the first failed field (up to 500 characters)
--   diagnostic_context -- JSON object with every failed field (field, rule, rows, values, keys)
-- The 10-parameter variants, etl.error_log and its rows are not changed. The number of parameters
-- differs, so the calls of the two variants cannot be confused.
-- Preconditions (STOP otherwise): 010 and 012 applied; etl.error_log.bad_value is text and
-- etl.error_log.diagnostic_context is jsonb; etl.error_log has no UPDATE trigger (as read from dwh on
-- 05.11.2026, i06_step2_run_journal.sql).
-- Privileges: EXECUTE only for the owner and dwh_etl; default privileges removed explicitly (rule since 005).
-- Requires 001-013. Run as the owner of schema etl:
--   psql -1 -v ON_ERROR_STOP=1 -f 014_load_error_diagnostics.sql
DO $pre$
BEGIN
  SET LOCAL search_path = pg_catalog;
  IF to_regprocedure('etl.object_run_finish(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text)') IS NULL
     OR to_regprocedure('etl.object_ods_run_finish(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text)') IS NULL THEN
    RAISE EXCEPTION 'STOP: 010 and 012 must be applied first';
  END IF;
  IF to_regprocedure('etl.object_run_finish(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text, text, jsonb)') IS NOT NULL
     OR to_regprocedure('etl.object_ods_run_finish(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text, text, jsonb)') IS NOT NULL THEN
    RAISE EXCEPTION 'STOP: 014 is already applied';
  END IF;
  IF (SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a
       WHERE a.attrelid = 'etl.error_log'::regclass AND a.attname = 'bad_value' AND NOT a.attisdropped) IS DISTINCT FROM 'text'
     OR (SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a
          WHERE a.attrelid = 'etl.error_log'::regclass AND a.attname = 'diagnostic_context' AND NOT a.attisdropped) IS DISTINCT FROM 'jsonb' THEN
    RAISE EXCEPTION 'STOP: etl.error_log.bad_value must be text and diagnostic_context jsonb';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgrelid = 'etl.error_log'::regclass AND NOT t.tgisinternal
               AND (t.tgtype & 16) <> 0) THEN
    RAISE EXCEPTION 'STOP: etl.error_log has an UPDATE trigger; read it before applying 014';
  END IF;
END
$pre$;

-- Shared check of the two new parameters.
CREATE FUNCTION etl.load_error_diagnostics_valid(p_ok boolean, p_bad_value text, p_diagnostic jsonb)
RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS $fn$
  SELECT CASE
    WHEN p_ok IS NOT FALSE THEN p_bad_value IS NULL AND p_diagnostic IS NULL
    ELSE p_diagnostic IS NULL
         OR (jsonb_typeof(p_diagnostic) = 'object' AND octet_length(p_diagnostic::text) <= 65536)
  END
$fn$;

CREATE FUNCTION etl.object_run_finish(p_run_id bigint, p_ok boolean, p_rows_read bigint, p_rows_inserted bigint,
  p_rows_updated bigint, p_rows_unchanged bigint, p_error_code text, p_error_message text, p_record_key jsonb,
  p_column_name text, p_bad_value text, p_diagnostic jsonb)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, etl AS $fn$
DECLARE
  v_job_run bigint;
  v_status text;
BEGIN
  IF NOT etl.load_error_diagnostics_valid(p_ok, p_bad_value, p_diagnostic) THEN
    RAISE EXCEPTION 'Invalid run diagnostics';
  END IF;
  SELECT job_run_id INTO v_job_run FROM etl.object_run WHERE run_id = p_run_id;
  v_status := etl.object_run_finish(p_run_id, p_ok, p_rows_read, p_rows_inserted, p_rows_updated, p_rows_unchanged,
    p_error_code, p_error_message, p_record_key, p_column_name);
  IF NOT p_ok AND (p_bad_value IS NOT NULL OR p_diagnostic IS NOT NULL) THEN
    UPDATE etl.error_log
       SET bad_value = left(p_bad_value, 500), diagnostic_context = p_diagnostic
     WHERE error_id = (SELECT max(e.error_id) FROM etl.error_log e
                        WHERE e.job_run_id = v_job_run AND e.error_stage = 'OBJECT_LOAD');
    IF NOT FOUND THEN RAISE EXCEPTION 'Error record of the run not found'; END IF;
  END IF;
  RETURN v_status;
END;
$fn$;

CREATE FUNCTION etl.object_ods_run_finish(p_ods_run_id bigint, p_ok boolean, p_rows_read bigint,
  p_rows_inserted bigint, p_rows_updated bigint, p_rows_unchanged bigint, p_error_code text, p_error_message text,
  p_record_key jsonb, p_column_name text, p_bad_value text, p_diagnostic jsonb)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, etl AS $fn$
DECLARE
  v_job_run bigint;
  v_status text;
BEGIN
  IF NOT etl.load_error_diagnostics_valid(p_ok, p_bad_value, p_diagnostic) THEN
    RAISE EXCEPTION 'Invalid run diagnostics';
  END IF;
  SELECT job_run_id INTO v_job_run FROM etl.object_ods_run WHERE ods_run_id = p_ods_run_id;
  v_status := etl.object_ods_run_finish(p_ods_run_id, p_ok, p_rows_read, p_rows_inserted, p_rows_updated,
    p_rows_unchanged, p_error_code, p_error_message, p_record_key, p_column_name);
  IF NOT p_ok AND (p_bad_value IS NOT NULL OR p_diagnostic IS NOT NULL) THEN
    UPDATE etl.error_log
       SET bad_value = left(p_bad_value, 500), diagnostic_context = p_diagnostic
     WHERE error_id = (SELECT max(e.error_id) FROM etl.error_log e
                        WHERE e.job_run_id = v_job_run AND e.error_stage = 'ODS_LOAD');
    IF NOT FOUND THEN RAISE EXCEPTION 'Error record of the ODS run not found'; END IF;
  END IF;
  RETURN v_status;
END;
$fn$;

REVOKE ALL ON FUNCTION
  etl.load_error_diagnostics_valid(boolean, text, jsonb),
  etl.object_run_finish(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text, text, jsonb),
  etl.object_ods_run_finish(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text, text, jsonb)
FROM PUBLIC;

-- Default privileges of schema etl (rule since 005): remove every grant on the new functions except
-- the owner's and report it, then grant only what is intended.
DO $acl$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT 'FUNCTION ' || p.oid::regprocedure::text AS obj, x.grantee
      FROM pg_proc p, aclexplode(p.proacl) x
     WHERE p.oid IN ('etl.load_error_diagnostics_valid(boolean, text, jsonb)'::regprocedure,
                     'etl.object_run_finish(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text, text, jsonb)'::regprocedure,
                     'etl.object_ods_run_finish(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text, text, jsonb)'::regprocedure)
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
  etl.object_run_finish(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text, text, jsonb),
  etl.object_ods_run_finish(bigint, boolean, bigint, bigint, bigint, bigint, text, text, jsonb, text, text, jsonb)
TO dwh_etl;

SELECT 'MIGRATION_014_DONE' AS result;
