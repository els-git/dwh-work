-- Catalog fingerprint for before/after comparison: object count and md5 over
-- relations, functions, triggers, constraints, namespaces and secret_store
-- columns. Read-only; no row contents. Output: <count>:<md5>
SELECT count(*) || ':' || md5(string_agg(x, '|' ORDER BY x)) FROM (
  SELECT n.nspname || '.' || c.relname || ':' || c.relkind::text AS x
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
  UNION ALL SELECT 'f:' || n.nspname || '.' || p.proname
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
  UNION ALL SELECT 't:' || tgname FROM pg_trigger WHERE NOT tgisinternal
  UNION ALL SELECT 'c:' || conname
    FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
   WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
  UNION ALL SELECT 'n:' || nspname FROM pg_namespace
  UNION ALL SELECT 'a:' || c.relname || '.' || a.attname
    FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'secret_store' AND a.attnum > 0 AND NOT a.attisdropped
) s;
