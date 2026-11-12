-- I16 step 7: rows of every table and the number of objects per schema (for the comparison PC <-> server).
SELECT line FROM (
  SELECT 'rows|' || n.nspname || '.' || c.relname || '|' ||
         (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I.%I', n.nspname, c.relname),
                                              false, true, '')))[1]::text AS line
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE c.relkind IN ('r', 'p') AND n.nspname NOT IN ('pg_catalog', 'information_schema')
     AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp%'
  UNION ALL
  SELECT 'objects|' || n.nspname || '|' || c.relkind::text || '|' || count(*)
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg_toast%'
     AND n.nspname NOT LIKE 'pg_temp%' AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'i')
   GROUP BY n.nspname, c.relkind
  UNION ALL
  SELECT 'functions|' || n.nspname || '|' || count(*)
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
   GROUP BY n.nspname
) t ORDER BY line;
