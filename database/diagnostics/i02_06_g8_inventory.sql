-- I02-06 step 2: read-only inventory of the old CRM_RETAIL bindings without profile (G8).
-- Run on the server as postgres in a read-only transaction (dwh_app cannot read secret_store):
--   sudo runuser -u postgres -- psql -X -d dwh -v ON_ERROR_STOP=1 -f /opt/dwh/current/src/database/diagnostics/i02_06_g8_inventory.sql
-- Output: bindings, their journal events, audit events of CRM_RETAIL on 27-31.10.2026. No credential values:
-- the journal and the audit hold none; secret_ref is a file name, the ciphertext digest is shortened.
SET default_transaction_read_only = on;
BEGIN;
SHOW transaction_read_only;

\echo '== 1. bindings without profile (G8)'
SELECT s.code, b.state, b.secret_ref, b.create_operation_id, b.key_id, left(b.ciphertext_sha256, 8) AS sha8,
       b.created_at, b.activated_at, b.revoked_at
  FROM secret_store.binding b JOIN meta.source_system s ON s.source_system_id = b.source_system_id
 WHERE b.profile_code IS NULL
 ORDER BY b.created_at;

\echo '== 2. journal events of these bindings (any operation that targets them)'
SELECT e.create_operation_id, e.operation_id, e.operation_type, e.phase, e.code, e.actor_kind, e.actor_ref,
       e.occurred_at
  FROM secret_store.operation_event e
 WHERE e.create_operation_id IN (SELECT create_operation_id FROM secret_store.binding WHERE profile_code IS NULL)
 ORDER BY e.occurred_at, e.phase;

\echo '== 3. all journal events of CRM_RETAIL on 27-31.10.2026'
SELECT e.operation_type, e.phase, e.code, e.actor_kind, e.actor_ref, e.occurred_at, e.create_operation_id
  FROM secret_store.operation_event e JOIN meta.source_system s ON s.source_system_id = e.source_system_id
 WHERE s.code = 'CRM_RETAIL' AND e.occurred_at >= '2026-10-27' AND e.occurred_at < '2026-11-01'
 ORDER BY e.occurred_at;

\echo '== 4. audit events about CRM_RETAIL or secrets on 27-31.10.2026'
SELECT a.occurred_at, a.event_type, a.entity_type, a.entity_key, a.action, a.outcome, a.source,
       a.actor_username, left(a.details::text, 300) AS details
  FROM audit.security_event a
 WHERE a.occurred_at >= '2026-10-27' AND a.occurred_at < '2026-11-01'
   AND (a.event_type ILIKE '%SECRET%' OR a.entity_key IN
        (SELECT source_system_id::text FROM meta.source_system WHERE code = 'CRM_RETAIL'))
 ORDER BY a.occurred_at
 LIMIT 100;

\echo '== 5. readers: functions that could use a binding without profile'
SELECT count(*) AS read_paths_requiring_profile
  FROM pg_proc p WHERE p.oid IN ('etl.source_secret_read_begin(text,bigint)'::regprocedure,
                                 'app.integration_source_secret_read_begin(text,bigint)'::regprocedure)
   AND pg_get_functiondef(p.oid) LIKE '%profile_code = ''SOURCE''%';

ROLLBACK;
