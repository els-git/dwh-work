-- I05 rollback of 009: drop stg.operation_collector_asan_pension_requests and reopen its proposal.
-- Refuses and changes nothing if the table has rows or a published configuration of any
-- object targets it. Run as the owner of schema stg:
--   psql -v ON_ERROR_STOP=1 -f 009_target_operation_collector_asan.down.sql
BEGIN;
LOCK TABLE stg.operation_collector_asan_pension_requests IN ACCESS EXCLUSIVE MODE;
DO $do$
DECLARE
  v_id bigint;
  v_object bigint;
BEGIN
  IF EXISTS (SELECT 1 FROM stg.operation_collector_asan_pension_requests) THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: the table has rows';
  END IF;
  IF EXISTS (SELECT 1 FROM meta.object_config_current oc JOIN meta.object_config_version v ON v.version_id = oc.version_id
              WHERE v.target_schema = 'stg' AND v.target_table = 'operation_collector_asan_pension_requests') THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: a published configuration targets the table';
  END IF;
  SELECT proposal_id, source_object_id INTO v_id, v_object FROM meta.target_change_proposal
   WHERE target_schema = 'stg' AND target_table = 'operation_collector_asan_pension_requests'
     AND change_kind = 'CREATE_TABLE' AND status = 'APPLIED'
     AND encode(sha256(convert_to(ddl, 'UTF8')), 'hex') = 'b964a1bcd172ee4136728d1350b2ba78c769b5e6e2e0eff5d238dd0f321850ae'
   ORDER BY proposal_id DESC LIMIT 1;
  IF v_id IS NULL THEN RAISE EXCEPTION 'ROLLBACK REFUSED: applied proposal not found'; END IF;
  DROP TABLE stg.operation_collector_asan_pension_requests;
  UPDATE meta.target_change_proposal SET status = 'PROPOSED', updated_at = clock_timestamp() WHERE proposal_id = v_id;
  PERFORM audit.log_security_event('INTEGRATION_TARGET_PROPOSAL_UNAPPLIED', 'SOURCE_OBJECT', v_object::text,
    'ROLLBACK', 'SUCCESS', jsonb_build_object('proposal_id', v_id, 'package', '009'));
  RAISE NOTICE 'REOPENED_PROPOSAL %', v_id;
END
$do$;
COMMIT;
