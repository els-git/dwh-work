-- I18 check of 020: the old CRM contour is removed, CRM_RETAIL stays as an ordinary source.
-- SELECT only; catalog and the tables dwh_app may read. Runs as dwh_app (dwh_sql_package.py) or as the owner.
-- Expected: 8 results, every one PASS. Check 019 stays 8/8.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH checks AS (
  SELECT 1 AS n, 'marker meta.legacy_crm_removed() exists, not executable by PUBLIC' AS check_name,
    to_regprocedure('meta.legacy_crm_removed()') IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM pg_proc p, aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) x
                     WHERE p.oid = to_regprocedure('meta.legacy_crm_removed()') AND x.grantee = 0) AS ok
  UNION ALL SELECT 2, 'no stg/ods tables crm_retail_* and no dm retail_application objects',
    NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE (n.nspname IN ('stg', 'ods') AND c.relname ~ '^crm_retail_')
                    OR (n.nspname = 'dm' AND c.relname ~ '^(v_)?retail_application'))
  UNION ALL SELECT 3, 'no function dm.refresh_retail_application',
    NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                 WHERE n.nspname = 'dm' AND p.proname = 'refresh_retail_application')
  UNION ALL SELECT 4, 'no contracts and mapping rules of CRM_RETAIL (the source is not legacy)',
    NOT EXISTS (SELECT 1 FROM meta.column_contract WHERE source_system_code = 'CRM_RETAIL')
    AND NOT EXISTS (SELECT 1 FROM meta.mapping_rule WHERE source_system_code = 'CRM_RETAIL')
  UNION ALL SELECT 5, 'no jobs of the old contour',
    NOT EXISTS (SELECT 1 FROM etl.job WHERE code ~ '^(EXTRACT|TRANSFORM)_CRM_RETAIL_' OR code = 'REFRESH_DM_RETAIL_APPLICATION')
  UNION ALL SELECT 6, 'only object load runs are left',
    NOT EXISTS (SELECT 1 FROM etl.batch_run WHERE pipeline_code IS DISTINCT FROM 'OBJECT_LOAD')
  UNION ALL SELECT 7, 'no DQ rules',
    NOT EXISTS (SELECT 1 FROM dq.v_current_rule)
  UNION ALL SELECT 8, 'CRM_RETAIL and its objects stay; no AI whitelist row for the removed view',
    EXISTS (SELECT 1 FROM meta.source_system WHERE code = 'CRM_RETAIL')
    AND EXISTS (SELECT 1 FROM meta.source_object o JOIN meta.source_system s ON s.source_system_id = o.source_system_id
                 WHERE s.code = 'CRM_RETAIL')
    AND NOT EXISTS (SELECT 1 FROM meta.ai_report_object WHERE schema_name = 'dm' AND object_name = 'v_retail_application_current')
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
