-- I04 step 2: structure of the schema snapshot tables and who may write them.
-- Read-only: catalog metadata and row counts only, no table contents.
SELECT 'column | ' || c.relname || ' | ' || a.attnum || ' | ' || a.attname || ' | '
    || format_type(a.atttypid, a.atttypmod) || ' | not_null=' || a.attnotnull::text
    || coalesce(' | default=' || pg_get_expr(d.adbin, d.adrelid), '')
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
  LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
 WHERE n.nspname = 'meta' AND c.relname IN ('metadata_capture', 'source_object_snapshot', 'source_column_snapshot')
 ORDER BY c.relname, a.attnum;

SELECT 'constraint | ' || conrelid::regclass || ' | ' || conname || ' | ' || pg_get_constraintdef(oid)
  FROM pg_constraint
 WHERE conrelid IN ('meta.metadata_capture'::regclass, 'meta.source_object_snapshot'::regclass, 'meta.source_column_snapshot'::regclass)
 ORDER BY 1;

SELECT 'trigger | ' || tgrelid::regclass || ' | ' || tgname || ' | ' || pg_get_triggerdef(oid)
  FROM pg_trigger
 WHERE NOT tgisinternal
   AND tgrelid IN ('meta.metadata_capture'::regclass, 'meta.source_object_snapshot'::regclass, 'meta.source_column_snapshot'::regclass)
 ORDER BY 1;

SELECT 'grant | ' || table_name || ' | ' || grantee || ' | ' || string_agg(privilege_type, ',' ORDER BY privilege_type)
  FROM information_schema.role_table_grants
 WHERE table_schema = 'meta'
   AND table_name IN ('metadata_capture', 'source_object_snapshot', 'source_column_snapshot', 'v_latest_source_structure')
 GROUP BY table_name, grantee
 ORDER BY 1;

SELECT 'view | v_latest_source_structure | ' || replace(pg_get_viewdef('meta.v_latest_source_structure'::regclass, true), E'\n', ' ');

SELECT 'rows | metadata_capture=' || (SELECT count(*) FROM meta.metadata_capture)
    || ' | source_object_snapshot=' || (SELECT count(*) FROM meta.source_object_snapshot)
    || ' | source_column_snapshot=' || (SELECT count(*) FROM meta.source_column_snapshot);

SELECT 'source | ' || s.source_system_id || ' | ' || s.code || ' | ' || s.source_type || ' | active=' || s.is_active::text
    || ' | objects=' || (SELECT count(*) FROM meta.source_object o WHERE o.source_system_id = s.source_system_id)
  FROM meta.source_system s ORDER BY s.source_system_id;

SELECT 'object | ' || o.source_object_id || ' | system=' || o.source_system_id || ' | ' || o.source_schema || '.' || o.source_object
    || ' | ' || o.object_type || ' | active=' || o.is_active::text
  FROM meta.source_object o
 WHERE o.source_system_id = (SELECT source_system_id FROM meta.source_system WHERE code = 'OPERATION_COLLECTOR')
 ORDER BY o.source_object_id;
