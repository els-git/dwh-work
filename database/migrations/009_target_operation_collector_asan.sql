-- I05 (step 7): target table of OPERATION_COLLECTOR public.asan_pension_requests.
--
-- Applies the change proposal made in the UI on 04.11.2026 (step 6; decision M5:
-- DDL is applied only as a database package, E0 -> E1 -> E2). The package executes
-- the DDL text STORED in meta.target_change_proposal, after checking that it is the
-- reviewed text below (SHA-256 of the stored text), and marks the proposal APPLIED.
-- Reviewed DDL (SHA-256 b964a1bcd172ee4136728d1350b2ba78c769b5e6e2e0eff5d238dd0f321850ae):
--   -- I05 change proposal for object 16 (public.asan_pension_requests); apply only as a database package (E0 -> E1 -> E2).
--   CREATE TABLE "stg"."operation_collector_asan_pension_requests" (
--     "id" bigint NOT NULL,
--     "pin" varchar(16),
--     "name" varchar(256),
--     "surname" varchar(256),
--     "meta_data" jsonb,
--     "patronymic" varchar(256),
--     "birth_date" varchar(16),
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
--     CONSTRAINT "pk_operation_collector_asan_pension_requests" PRIMARY KEY ("id")
--   );
--
-- Privileges: none granted here. The new table gets the default privileges of
-- schema stg for its owner (dwh_etl read/write, read on 04.11.2026 in I05 step 2);
-- dwh_app gets nothing. The check script verifies both.
-- Requires 008. Run as the owner of schema stg (and of meta):
--   psql -1 -v ON_ERROR_STOP=1 -f 009_target_operation_collector_asan.sql
DO $do$
DECLARE
  p meta.target_change_proposal;
  n integer;
BEGIN
  IF current_user::regrole <> (SELECT nspowner FROM pg_namespace WHERE nspname = 'stg')::regrole THEN
    RAISE EXCEPTION 'STOP: run as the owner of schema stg (current user %)', current_user;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace
              WHERE s.nspname = 'stg' AND c.relname = 'operation_collector_asan_pension_requests') THEN
    RAISE EXCEPTION 'STOP: stg.operation_collector_asan_pension_requests already exists';
  END IF;
  SELECT count(*) INTO n FROM meta.target_change_proposal
   WHERE target_schema = 'stg' AND target_table = 'operation_collector_asan_pension_requests'
     AND change_kind = 'CREATE_TABLE' AND status = 'PROPOSED';
  IF n <> 1 THEN
    RAISE EXCEPTION 'STOP: expected exactly one open CREATE_TABLE proposal for the table, found %', n;
  END IF;
  SELECT * INTO p FROM meta.target_change_proposal
   WHERE target_schema = 'stg' AND target_table = 'operation_collector_asan_pension_requests'
     AND change_kind = 'CREATE_TABLE' AND status = 'PROPOSED'
   FOR UPDATE;
  IF encode(sha256(convert_to(p.ddl, 'UTF8')), 'hex') <> 'b964a1bcd172ee4136728d1350b2ba78c769b5e6e2e0eff5d238dd0f321850ae' THEN
    RAISE EXCEPTION 'STOP: proposal % DDL differs from the reviewed text', p.proposal_id;
  END IF;
  EXECUTE p.ddl;
  UPDATE meta.target_change_proposal SET status = 'APPLIED', updated_at = clock_timestamp()
   WHERE proposal_id = p.proposal_id;
  PERFORM audit.log_security_event('INTEGRATION_TARGET_PROPOSAL_APPLIED', 'SOURCE_OBJECT', p.source_object_id::text,
    'APPLY', 'SUCCESS', jsonb_build_object('proposal_id', p.proposal_id, 'package', '009',
    'target', 'stg.operation_collector_asan_pension_requests', 'ddl_sha256', 'b964a1bcd172ee4136728d1350b2ba78c769b5e6e2e0eff5d238dd0f321850ae'));
  RAISE NOTICE 'APPLIED_PROPOSAL %', p.proposal_id;
END
$do$;
COMMENT ON TABLE stg.operation_collector_asan_pension_requests IS
  'I05 target of OPERATION_COLLECTOR public.asan_pension_requests (package 009, change proposal)';
