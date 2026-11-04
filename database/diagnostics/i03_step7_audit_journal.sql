-- I03 step 7: audit trail of the source connection acceptance (read-only).
-- Privileges of dwh_app first, then today's I03 audit events of the sources
-- (event, action, outcome, operation id, key id; no secrets are ever logged).
SELECT 'privilege | schema audit USAGE=' || has_schema_privilege('audit', 'USAGE')::text
    || ' | audit.security_event SELECT='
    || CASE WHEN has_schema_privilege('audit', 'USAGE')
            THEN has_table_privilege('audit.security_event', 'SELECT')::text ELSE 'false' END
    || ' | schema secret_store USAGE=' || has_schema_privilege('secret_store', 'USAGE')::text;

SELECT 'event | ' || to_char(occurred_at, 'YYYY-MM-DD HH24:MI:SS') || ' | source=' || entity_key
    || ' | ' || event_type || ' | ' || action || ' | ' || outcome
    || ' | actor=' || coalesce(actor_username, '?')
    || ' | op=' || coalesce(details ->> 'operation_id', '-')
    || ' | key_id=' || coalesce(details ->> 'key_id', '-')
  FROM audit.security_event
 WHERE event_type LIKE 'INTEGRATION_SOURCE_%'
   AND occurred_at >= date '2026-11-04'
 ORDER BY security_event_id
 LIMIT 100;
