-- I03-05 step 2: read-only inventory for changing the schema/name of an unused source object.
-- Server: sudo /opt/dwh/current/venv/bin/python /opt/dwh/current/src/tools/server/dwh_readonly_sql.py <this file>
-- (dwh_app, read-only transaction; catalog only, no business data).
SELECT 'FK ' || c.conrelid::regclass || '.' || a.attname || ' -> meta.source_object' || ' ON DELETE ' ||
       CASE c.confdeltype WHEN 'a' THEN 'NO ACTION' WHEN 'r' THEN 'RESTRICT' WHEN 'c' THEN 'CASCADE'
                          WHEN 'n' THEN 'SET NULL' ELSE 'SET DEFAULT' END AS item
  FROM pg_constraint c JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
 WHERE c.contype = 'f' AND c.confrelid = 'meta.source_object'::regclass
 ORDER BY 1;
SELECT 'CONSTRAINT meta.source_object ' || conname || ': ' || pg_get_constraintdef(oid) AS item
  FROM pg_constraint WHERE conrelid = 'meta.source_object'::regclass ORDER BY conname;
SELECT 'TRIGGER meta.source_object ' || tgname || ' -> ' || tgfoid::regproc AS item
  FROM pg_trigger WHERE tgrelid = 'meta.source_object'::regclass AND NOT tgisinternal ORDER BY tgname;
SELECT 'COLUMN meta.source_object.' || attname || ' ' || format_type(atttypid, atttypmod) ||
       CASE WHEN attnotnull THEN ' NOT NULL' ELSE '' END AS item
  FROM pg_attribute WHERE attrelid = 'meta.source_object'::regclass AND attnum > 0 AND NOT attisdropped ORDER BY attnum;
SELECT 'FUNCTION ' || p.oid::regprocedure || E'\n' || pg_get_functiondef(p.oid) AS item
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname = 'app' AND p.proname IN ('integration_source_object_update', 'integration_source_object_create',
                                            'integration_source_object_set_active')
 ORDER BY 1;
