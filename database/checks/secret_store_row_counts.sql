-- Row counts of secret_store by state (no contents). Needs the schema owner.
-- Output lines: binding:<STATE>=<n>, operation_event=<n>, source_system=<n>
SELECT 'binding:' || state || '=' || count(*) FROM secret_store.binding GROUP BY state
UNION ALL SELECT 'operation_event=' || count(*) FROM secret_store.operation_event
UNION ALL SELECT 'source_system=' || count(*) FROM meta.source_system
ORDER BY 1;
