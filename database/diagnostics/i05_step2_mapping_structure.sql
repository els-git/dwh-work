-- I05 step 2: structure of the object, contract and mapping catalogs and of the
-- target schemas stg/ods. Read-only: catalog metadata and row counts only.
SELECT 'column | ' || c.relname || ' | ' || a.attnum || ' | ' || a.attname || ' | '
    || format_type(a.atttypid, a.atttypmod) || ' | not_null=' || a.attnotnull::text
    || coalesce(' | default=' || pg_get_expr(d.adbin, d.adrelid), '')
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
  LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
 WHERE n.nspname = 'meta' AND c.relname IN ('source_object', 'column_contract', 'mapping_rule', 'mapping_source_ref')
 ORDER BY c.relname, a.attnum;

SELECT 'constraint | ' || conrelid::regclass || ' | ' || conname || ' | ' || pg_get_constraintdef(oid)
  FROM pg_constraint
 WHERE conrelid IN (SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                     WHERE n.nspname = 'meta' AND c.relname IN ('source_object', 'column_contract', 'mapping_rule', 'mapping_source_ref'))
 ORDER BY 1;

SELECT 'trigger | ' || tgrelid::regclass || ' | ' || tgname
  FROM pg_trigger
 WHERE NOT tgisinternal
   AND tgrelid IN (SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'meta' AND c.relname IN ('source_object', 'column_contract', 'mapping_rule', 'mapping_source_ref'))
 ORDER BY 1;

SELECT 'schema | ' || n.nspname || ' | owner=' || pg_get_userbyid(n.nspowner)
    || ' | etl_usage=' || has_schema_privilege('dwh_etl', n.oid, 'USAGE')::text
    || ' | etl_create=' || has_schema_privilege('dwh_etl', n.oid, 'CREATE')::text
    || ' | app_usage=' || has_schema_privilege('dwh_app', n.oid, 'USAGE')::text
  FROM pg_namespace n WHERE n.nspname IN ('stg', 'ods', 'meta', 'app', 'etl') ORDER BY 1;

SELECT 'default_acl | ' || n.nspname || ' | ' || d.defaclobjtype || ' | ' || array_to_string(d.defaclacl, ' ')
  FROM pg_default_acl d JOIN pg_namespace n ON n.oid = d.defaclnamespace
 WHERE n.nspname IN ('stg', 'ods') ORDER BY 1;

SELECT 'table | ' || n.nspname || '.' || c.relname || ' | kind=' || c.relkind
    || ' | columns=' || (SELECT count(*) FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped)
    || ' | pk=' || coalesce((SELECT string_agg(a.attname, ',' ORDER BY a.attnum) FROM pg_index i
                   JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
                  WHERE i.indrelid = c.oid AND i.indisprimary), '-')
    || ' | owner=' || pg_get_userbyid(c.relowner)
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname IN ('stg', 'ods') AND c.relkind IN ('r', 'p', 'v')
 ORDER BY 1;

-- One STG table in full, as the pattern for new targets.
SELECT 'stg_pattern | ' || a.attnum || ' | ' || a.attname || ' | ' || format_type(a.atttypid, a.atttypmod)
    || ' | not_null=' || a.attnotnull::text || coalesce(' | default=' || pg_get_expr(d.adbin, d.adrelid), '')
  FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
 WHERE a.attrelid = to_regclass('stg.crm_retail_integration_log') AND a.attnum > 0 AND NOT a.attisdropped
 ORDER BY a.attnum;

-- Legacy catalogs per source (decision M7: which sources are legacy).
SELECT 'legacy | ' || s.code
    || ' | contracts=' || (SELECT count(*) FROM meta.column_contract cc WHERE cc.source_system_id = s.source_system_id)
    || ' | mappings=' || (SELECT count(*) FROM meta.mapping_rule mr WHERE mr.source_system_id = s.source_system_id)
  FROM meta.source_system s ORDER BY s.source_system_id;
