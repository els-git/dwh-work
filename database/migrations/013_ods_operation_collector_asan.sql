-- I06-05 (step 7): ODS table of OPERATION_COLLECTOR public.asan_pension_requests.
--
-- Applies the change proposal made in the UI on 11.11.2026 (tab "STG -> ODS", proposal #3;
-- decision O5: DDL is applied only as a database package, E0 -> E1 -> E2). The package executes
-- the DDL text STORED in meta.target_change_proposal, after checking that it is the reviewed text
-- below (SHA-256 of the stored text), and marks the proposal APPLIED. Other OPEN CREATE_TABLE
-- proposals of the ODS line for the same table (proposal #2: birth_date as text, superseded by #3)
-- are marked REJECTED with an audit event; nothing else changes.
-- Reviewed DDL (SHA-256 467a167671b2e4e8a10fafa9457e563681c0f969364092e909d5683e3819abaf):
--   -- I06-05 change proposal for ODS of object 16 (from stg.operation_collector_asan_pension_requests); apply only as a database package (E0 -> E1 -> E2).
--   CREATE TABLE "ods"."operation_collector_asan_pension_requests" (
--     "id" bigint NOT NULL,
--     "pin" varchar(16),
--     "name" varchar(256),
--     "surname" varchar(256),
--     "meta_data" jsonb,
--     "patronymic" varchar(256),
--     "birth_date" date,
--     "is_success" boolean,
--     "is_cache" boolean,
--     "has_meta_data" boolean,
--     "is_paid" boolean,
--     "asan_response_status" varchar(32),
--     "message" text,
--     "interval_days" bigint,
--     "channel" varchar(64),
--     "product_type" varchar(64),
--     "created_at" timestamp,
--     "loaded_at" timestamptz,
--     CONSTRAINT "pk_operation_collector_asan_pension_requests" PRIMARY KEY ("id")
--   );
--
-- Privileges: none granted here. The new table gets the default privileges of schema ods for its
-- owner (dwh_etl read/write, read on 04.11.2026 in I05 step 2); dwh_app gets nothing. The check
-- script verifies both.
-- Requires 012. Run as the owner of schema ods (and of meta):
--   psql -1 -v ON_ERROR_STOP=1 -f 013_ods_operation_collector_asan.sql
DO $do$
DECLARE
  p meta.target_change_proposal;
  n integer;
  r record;
BEGIN
  IF current_user::regrole <> (SELECT nspowner FROM pg_namespace WHERE nspname = 'ods')::regrole THEN
    RAISE EXCEPTION 'STOP: run as the owner of schema ods (current user %)', current_user;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace
              WHERE s.nspname = 'ods' AND c.relname = 'operation_collector_asan_pension_requests') THEN
    RAISE EXCEPTION 'STOP: ods.operation_collector_asan_pension_requests already exists';
  END IF;
  SELECT count(*) INTO n FROM meta.target_change_proposal
   WHERE target_schema = 'ods' AND target_table = 'operation_collector_asan_pension_requests' AND change_kind = 'CREATE_TABLE' AND status = 'PROPOSED'
     AND ods_version_id IS NOT NULL
     AND encode(sha256(convert_to(ddl, 'UTF8')), 'hex') = '467a167671b2e4e8a10fafa9457e563681c0f969364092e909d5683e3819abaf';
  IF n <> 1 THEN
    RAISE EXCEPTION 'STOP: expected exactly one open ODS proposal with the reviewed DDL, found %', n;
  END IF;
  SELECT * INTO p FROM meta.target_change_proposal
   WHERE target_schema = 'ods' AND target_table = 'operation_collector_asan_pension_requests' AND change_kind = 'CREATE_TABLE' AND status = 'PROPOSED'
     AND ods_version_id IS NOT NULL
     AND encode(sha256(convert_to(ddl, 'UTF8')), 'hex') = '467a167671b2e4e8a10fafa9457e563681c0f969364092e909d5683e3819abaf'
   FOR UPDATE;
  EXECUTE p.ddl;
  UPDATE meta.target_change_proposal SET status = 'APPLIED', updated_at = clock_timestamp()
   WHERE proposal_id = p.proposal_id;
  PERFORM audit.log_security_event('INTEGRATION_TARGET_PROPOSAL_APPLIED', 'SOURCE_OBJECT', p.source_object_id::text,
    'APPLY', 'SUCCESS', jsonb_build_object('proposal_id', p.proposal_id, 'package', '013',
    'target', 'ods.operation_collector_asan_pension_requests', 'ddl_sha256', '467a167671b2e4e8a10fafa9457e563681c0f969364092e909d5683e3819abaf'));
  RAISE NOTICE 'APPLIED_PROPOSAL %', p.proposal_id;
  FOR r IN SELECT x.proposal_id, x.source_object_id FROM meta.target_change_proposal x
            WHERE x.target_schema = 'ods' AND x.target_table = 'operation_collector_asan_pension_requests' AND x.change_kind = 'CREATE_TABLE'
              AND x.status = 'PROPOSED' AND x.ods_version_id IS NOT NULL
            ORDER BY x.proposal_id FOR UPDATE LOOP
    UPDATE meta.target_change_proposal SET status = 'REJECTED', updated_at = clock_timestamp()
     WHERE proposal_id = r.proposal_id;
    PERFORM audit.log_security_event('INTEGRATION_TARGET_PROPOSAL_REJECTED', 'SOURCE_OBJECT', r.source_object_id::text,
      'UPDATE', 'SUCCESS', jsonb_build_object('proposal_id', r.proposal_id, 'package', '013',
      'reason', 'superseded by proposal ' || p.proposal_id));
    RAISE NOTICE 'SUPERSEDED_PROPOSAL_REJECTED %', r.proposal_id;
  END LOOP;
END
$do$;
COMMENT ON TABLE ods.operation_collector_asan_pension_requests IS
  'I06-05 ODS target of OPERATION_COLLECTOR public.asan_pension_requests (package 013, change proposal)';
