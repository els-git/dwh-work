-- I13-15 step 1: which kinds of objects the audit journal refers to and how their keys look
-- (read only; counts and key shapes, no details of the events).
-- Run on the server as postgres in a read-only transaction:
--   sudo runuser -u postgres -- psql -X -d dwh -v ON_ERROR_STOP=1 < /opt/dwh/current/src/database/diagnostics/i13_15_audit_entity_inventory.sql
SET default_transaction_read_only = on;
BEGIN;
SHOW transaction_read_only;

\echo '== 1. object kinds in the journal: events, distinct keys, key shape (digits only or text), first and last event'
SELECT coalesce(entity_type, '<null>') AS entity_type, count(*) AS events, count(DISTINCT entity_key) AS keys,
       count(*) FILTER (WHERE entity_key ~ '^[0-9]+$') AS numeric_keys,
       count(*) FILTER (WHERE entity_key IS NULL) AS null_keys,
       min(occurred_at)::date AS first_at, max(occurred_at)::date AS last_at
  FROM audit.security_event GROUP BY 1 ORDER BY 2 DESC;

\echo '== 2. up to 3 example keys per object kind (keys are ids or codes, not secrets)'
SELECT entity_type, string_agg(entity_key, ' | ' ORDER BY entity_key) AS example_keys
  FROM (SELECT DISTINCT entity_type, left(entity_key, 60) AS entity_key FROM audit.security_event
         WHERE entity_key IS NOT NULL) d
 WHERE (SELECT count(*) FROM (SELECT DISTINCT left(e2.entity_key, 60) k FROM audit.security_event e2
         WHERE e2.entity_type = d.entity_type AND left(e2.entity_key, 60) < d.entity_key) z) < 3
 GROUP BY 1 ORDER BY 1;

\echo '== 3. numeric keys of the main kinds that no longer have a row in their table'
SELECT 'SOURCE_OBJECT' AS kind, count(DISTINCT e.entity_key) AS missing FROM audit.security_event e
 WHERE e.entity_type = 'SOURCE_OBJECT' AND e.entity_key ~ '^[0-9]+$'
   AND NOT EXISTS (SELECT 1 FROM meta.source_object o WHERE o.source_object_id = e.entity_key::bigint)
UNION ALL
SELECT 'SOURCE_SYSTEM', count(DISTINCT e.entity_key) FROM audit.security_event e
 WHERE e.entity_type = 'SOURCE_SYSTEM' AND e.entity_key ~ '^[0-9]+$'
   AND NOT EXISTS (SELECT 1 FROM meta.source_system s WHERE s.source_system_id = e.entity_key::bigint)
UNION ALL
SELECT 'DM_MART', count(DISTINCT e.entity_key) FROM audit.security_event e
 WHERE e.entity_type = 'DM_MART' AND e.entity_key ~ '^[0-9]+$'
   AND NOT EXISTS (SELECT 1 FROM meta.dm_mart m WHERE m.mart_id = e.entity_key::bigint)
UNION ALL
SELECT 'USER_ACCOUNT', count(DISTINCT e.entity_key) FROM audit.security_event e
 WHERE e.entity_type = 'USER_ACCOUNT' AND e.entity_key ~ '^[0-9]+$'
   AND NOT EXISTS (SELECT 1 FROM app.user_account u WHERE u.user_id = e.entity_key::bigint);

\echo '== 4. columns that can name the objects (to build the name)'
SELECT table_schema || '.' || table_name AS tbl, string_agg(column_name, ', ' ORDER BY ordinal_position) AS columns
  FROM information_schema.columns
 WHERE (table_schema, table_name) IN (('meta', 'source_object'), ('meta', 'source_system'), ('meta', 'dm_mart'),
                                      ('meta', 'dm_entity'), ('app', 'user_account'), ('app', 'role'))
 GROUP BY 1 ORDER BY 1;

\echo '== 5. indexes of the journal (for the filter by name)'
SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'audit' AND tablename = 'security_event' ORDER BY 1;

ROLLBACK;
\echo '== I13_15_INVENTORY_DONE'
