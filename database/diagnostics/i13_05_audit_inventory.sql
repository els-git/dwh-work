-- I13-05 step 2: read-only inventory of the journals that the administrator will view.
-- Run on the server as postgres in a read-only transaction (dwh_app cannot read audit.security_event and
-- secret_store); the file is read by the shell of the user, so it can stay in /srv/dwh/incoming:
--   sudo runuser -u postgres -- psql -X -d dwh -v ON_ERROR_STOP=1 < /srv/dwh/incoming/i13_05_audit_inventory.sql
-- Output: structure, indexes, rights, counts, event types and the NAMES of keys in details. No values of
-- details, no secret_ref, no user names are printed.
SET default_transaction_read_only = on;
BEGIN;
SHOW transaction_read_only;

\echo '== 1. columns of the journals'
SELECT table_schema, table_name, ordinal_position AS n, column_name, data_type, is_nullable
  FROM information_schema.columns
 WHERE (table_schema, table_name) IN (('audit', 'security_event'), ('secret_store', 'operation_event'),
                                      ('secret_store', 'operation'))
 ORDER BY 1, 2, 3;

\echo '== 2. indexes'
SELECT schemaname, tablename, indexname, indexdef
  FROM pg_indexes
 WHERE (schemaname, tablename) IN (('audit', 'security_event'), ('secret_store', 'operation_event'),
                                   ('secret_store', 'operation'))
 ORDER BY 1, 2, 3;

\echo '== 3. rights on the journals and their schemas'
SELECT n.nspname AS schema, c.relname AS relation, c.relowner::regrole AS owner, c.relacl
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE (n.nspname, c.relname) IN (('audit', 'security_event'), ('secret_store', 'operation_event'),
                                  ('secret_store', 'operation'));
SELECT nspname AS schema, nspowner::regrole AS owner, nspacl FROM pg_namespace WHERE nspname IN ('audit', 'secret_store', 'app');

\echo '== 4. size, rows and period'
SELECT 'audit.security_event' AS journal, count(*) AS row_count, min(occurred_at) AS first_at, max(occurred_at) AS last_at,
       pg_size_pretty(pg_total_relation_size('audit.security_event')) AS size
  FROM audit.security_event
UNION ALL
SELECT 'secret_store.operation_event', count(*), min(occurred_at), max(occurred_at),
       pg_size_pretty(pg_total_relation_size('secret_store.operation_event'))
  FROM secret_store.operation_event;

\echo '== 5. audit: event types (count, outcomes, sources, entity types)'
SELECT event_type, count(*) AS n, string_agg(DISTINCT outcome, ',') AS outcomes,
       string_agg(DISTINCT source, ',') AS sources, string_agg(DISTINCT entity_type, ',') AS entity_types,
       max(occurred_at) AS last_at
  FROM audit.security_event GROUP BY event_type ORDER BY n DESC;

\echo '== 6. audit: account levels of actors and events without actor'
SELECT coalesce(actor_account_level, '(none)') AS actor_level, (actor_user_id IS NULL) AS no_actor, count(*) AS n
  FROM audit.security_event GROUP BY 1, 2 ORDER BY 3 DESC;

\echo '== 7. audit: names of keys in details by event type (names only, no values)'
SELECT e.event_type, k.key, jsonb_typeof(e.details -> k.key) AS type, count(*) AS n
  FROM audit.security_event e CROSS JOIN LATERAL jsonb_object_keys(
       CASE WHEN jsonb_typeof(e.details) = 'object' THEN e.details ELSE '{}'::jsonb END) AS k(key)
 GROUP BY 1, 2, 3 ORDER BY 1, 2;

\echo '== 8. audit: nested keys (second level, names only)'
SELECT k.key AS parent, k2.key AS child, count(*) AS n
  FROM audit.security_event e
  CROSS JOIN LATERAL jsonb_each(CASE WHEN jsonb_typeof(e.details) = 'object' THEN e.details ELSE '{}'::jsonb END) AS k(key, value)
  CROSS JOIN LATERAL jsonb_object_keys(CASE WHEN jsonb_typeof(k.value) = 'object' THEN k.value ELSE '{}'::jsonb END) AS k2(key)
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== 9. keys that look sensitive (any level of the first two)'
SELECT key, count(*) AS n FROM (
  SELECT k.key FROM audit.security_event e
    CROSS JOIN LATERAL jsonb_object_keys(CASE WHEN jsonb_typeof(e.details) = 'object' THEN e.details ELSE '{}'::jsonb END) AS k(key)
  UNION ALL
  SELECT k2.key FROM audit.security_event e
    CROSS JOIN LATERAL jsonb_each(CASE WHEN jsonb_typeof(e.details) = 'object' THEN e.details ELSE '{}'::jsonb END) AS k(key, value)
    CROSS JOIN LATERAL jsonb_object_keys(CASE WHEN jsonb_typeof(k.value) = 'object' THEN k.value ELSE '{}'::jsonb END) AS k2(key)
) s
 WHERE key ~* '(secret|passw|pwd|token|dsn|cipher|sha|hash|key_?id|ref|file|cred|private|salt)'
 GROUP BY key ORDER BY 2 DESC;

\echo '== 10. secret_store journal: operations, phases, codes, actor kinds'
SELECT operation_type, phase, code, actor_kind, count(*) AS n, max(occurred_at) AS last_at
  FROM secret_store.operation_event GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4;

\echo '== 11. existing functions that read the journals'
SELECT n.nspname || '.' || p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')' AS function,
       p.prosecdef AS security_definer, p.proacl
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname IN ('app', 'audit', 'secret_store')
   AND p.prosrc ~ 'FROM\s+(audit\.security_event|secret_store\.operation_event)'
 ORDER BY 1;

ROLLBACK;
\echo '== I13_05_INVENTORY_DONE'
