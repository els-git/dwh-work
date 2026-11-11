-- I06-05 rollback of 013: drop ods.operation_collector_asan_pension_requests and reopen its proposal.
-- Refuses and changes nothing if the table has rows or a published ODS configuration of any object
-- targets it. Proposals rejected as superseded by 013 stay REJECTED. Run as the owner of schema ods:
--   psql -v ON_ERROR_STOP=1 -f 013_ods_operation_collector_asan.down.sql
BEGIN;
LOCK TABLE ods.operation_collector_asan_pension_requests IN ACCESS EXCLUSIVE MODE;
DO $do$
DECLARE
  v_id bigint;
  v_object bigint;
BEGIN
  IF EXISTS (SELECT 1 FROM ods.operation_collector_asan_pension_requests) THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: the table has rows';
  END IF;
  IF EXISTS (SELECT 1 FROM meta.object_ods_current oc JOIN meta.object_ods_version v ON v.ods_version_id = oc.ods_version_id
              WHERE v.target_table = 'operation_collector_asan_pension_requests') THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: a published ODS configuration targets the table';
  END IF;
  SELECT proposal_id, source_object_id INTO v_id, v_object FROM meta.target_change_proposal
   WHERE target_schema = 'ods' AND target_table = 'operation_collector_asan_pension_requests' AND change_kind = 'CREATE_TABLE' AND status = 'APPLIED'
     AND encode(sha256(convert_to(ddl, 'UTF8')), 'hex') = '467a167671b2e4e8a10fafa9457e563681c0f969364092e909d5683e3819abaf'
   ORDER BY proposal_id DESC LIMIT 1;
  IF v_id IS NULL THEN RAISE EXCEPTION 'ROLLBACK REFUSED: applied proposal not found'; END IF;
  DROP TABLE ods.operation_collector_asan_pension_requests;
  UPDATE meta.target_change_proposal SET status = 'PROPOSED', updated_at = clock_timestamp() WHERE proposal_id = v_id;
  PERFORM audit.log_security_event('INTEGRATION_TARGET_PROPOSAL_UNAPPLIED', 'SOURCE_OBJECT', v_object::text,
    'ROLLBACK', 'SUCCESS', jsonb_build_object('proposal_id', v_id, 'package', '013'));
  RAISE NOTICE 'REOPENED_PROPOSAL %', v_id;
END
$do$;
COMMIT;
