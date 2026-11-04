-- I05 step 2 (part 2): default privileges and tables of stg/ods, STG pattern, legacy
-- catalogs per source. Continues i05_step2_mapping_structure.sql after its "char"
-- concatenation error. Read-only: catalog metadata and row counts only.
SELECT 'default_acl | ' || n.nspname || ' | ' || d.defaclobjtype::text || ' | ' || array_to_string(d.defaclacl, ' ')
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
