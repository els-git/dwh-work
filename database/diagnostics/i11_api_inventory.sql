-- I11 step 1: what the API connector and the test API service can build on.
-- Read only: structure, counts and column names/types; no business data.
-- Run on the server as postgres in a read-only transaction:
--   sudo runuser -u postgres -- psql -X -d dwh -v ON_ERROR_STOP=1 < /srv/dwh/incoming/i11_api_inventory.sql
SET default_transaction_read_only = on;
BEGIN;
SHOW transaction_read_only;

\echo '== 1. sources: type, connection kind and status, number of objects'
SELECT s.source_system_id, s.code, s.source_type, s.is_active,
       c.source_kind, c.status AS connection_status,
       (SELECT count(*) FROM meta.source_object o WHERE o.source_system_id = s.source_system_id) AS objects
  FROM meta.source_system s
  LEFT JOIN meta.source_connection c ON c.source_system_id = s.source_system_id
 ORDER BY s.source_system_id;

\echo '== 2. allowed values of source_kind and other checks of meta.source_connection'
SELECT conname, pg_get_constraintdef(oid) AS definition FROM pg_constraint
 WHERE conrelid = 'meta.source_connection'::regclass AND contype = 'c' ORDER BY 1;

\echo '== 3. columns and checks of meta.source_object (where per-object HTTP settings could live)'
SELECT column_name, data_type, is_nullable, column_default
  FROM information_schema.columns WHERE table_schema = 'meta' AND table_name = 'source_object'
 ORDER BY ordinal_position;
SELECT conname, pg_get_constraintdef(oid) AS definition FROM pg_constraint
 WHERE conrelid = 'meta.source_object'::regclass ORDER BY 1;

\echo '== 4. PROCESS_VIEWER objects and their columns in the latest snapshot (names and types only)'
SELECT o.source_object_id, o.source_schema || '.' || o.source_object AS object, o.is_active
  FROM meta.source_object o JOIN meta.source_system s USING (source_system_id)
 WHERE s.code = 'PROCESS_VIEWER' ORDER BY 1;
SELECT to_regclass('meta.v_latest_source_structure') IS NOT NULL AS has_structure_view \gset
\if :has_structure_view
SELECT to_jsonb(v) - 'source_system_id' AS latest_structure_row
  FROM meta.v_latest_source_structure v
 WHERE (to_jsonb(v) ->> 'source_system_id')::bigint =
       (SELECT source_system_id FROM meta.source_system WHERE code = 'PROCESS_VIEWER')
 ORDER BY 1::text;
\else
\echo 'meta.v_latest_source_structure: absent'
\endif

\echo '== 5. STG/ODS tables of PROCESS_VIEWER objects and estimated row counts'
SELECT n.nspname || '.' || c.relname AS table_name, c.reltuples::bigint AS estimated_rows
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind = 'r' AND n.nspname IN ('stg', 'ods') AND c.relname LIKE 'process_viewer%'
 ORDER BY 1;

\echo '== 6. SecretStore binding: columns and checks (is there a per-object slot?)'
SELECT column_name, data_type, is_nullable
  FROM information_schema.columns WHERE table_schema = 'secret_store' AND table_name = 'binding'
 ORDER BY ordinal_position;
SELECT conname, pg_get_constraintdef(oid) AS definition FROM pg_constraint
 WHERE conrelid = 'secret_store.binding'::regclass ORDER BY 1;

\echo '== 7. object configuration versions: columns of meta.object_config_version'
SELECT column_name, data_type, is_nullable
  FROM information_schema.columns WHERE table_schema = 'meta' AND table_name = 'object_config_version'
 ORDER BY ordinal_position;

ROLLBACK;
