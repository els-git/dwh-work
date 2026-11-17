-- I18 step 1: inventory of the old CRM contour in dwh (catalog and small meta tables, no business data).
-- Run on the server: dwh_readonly_sql.py (dwh_app, read-only transaction, rollback).
-- Small tables are guarded: ABSENT when the table does not exist, NO_SELECT when dwh_app may not read it.
-- Q1 relations whose name mentions crm or retail: schema, kind, owner, estimated rows
SELECT 'Q1' AS q, n.nspname, c.relname, c.relkind, pg_get_userbyid(c.relowner) AS owner, c.reltuples::bigint AS est_rows
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind IN ('r', 'v', 'm', 'p', 'f', 'S') AND c.relname ~* '(crm|retail)'
   AND n.nspname NOT IN ('pg_catalog', 'information_schema') ORDER BY 2, 3;
-- Q2 functions whose name or body mentions crm or retail
SELECT 'Q2' AS q, p.oid::regprocedure::text AS function, pg_get_userbyid(p.proowner) AS owner, p.prosecdef,
       length(p.prosrc) AS body_chars
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
   AND (p.proname ~* '(crm|retail)' OR p.prosrc ~* '(crm_retail|retail_application|CRM_RETAIL)') ORDER BY 2;
-- Q3 views and other relations that depend on the relations of Q1
SELECT DISTINCT 'Q3' AS q, sn.nspname || '.' || sc.relname AS crm_relation, dn.nspname || '.' || dc.relname AS dependent
  FROM pg_depend d JOIN pg_rewrite r ON r.oid = d.objid AND d.classid = 'pg_rewrite'::regclass
  JOIN pg_class dc ON dc.oid = r.ev_class JOIN pg_namespace dn ON dn.oid = dc.relnamespace
  JOIN pg_class sc ON sc.oid = d.refobjid JOIN pg_namespace sn ON sn.oid = sc.relnamespace
 WHERE sc.relname ~* '(crm|retail)' AND dc.oid <> sc.oid ORDER BY 2, 3;
-- Q4 foreign keys to meta.source_system, meta.source_object, etl.job, etl.batch_run, etl.job_run and to or from Q1 tables
SELECT 'Q4' AS q, c.conrelid::regclass::text AS from_table, c.confrelid::regclass::text AS to_table, c.conname,
       pg_get_constraintdef(c.oid) AS definition
  FROM pg_constraint c
 WHERE c.contype = 'f'
   AND (c.confrelid::regclass::text IN ('meta.source_system', 'meta.source_object', 'etl.job', 'etl.batch_run', 'etl.job_run')
        OR c.confrelid::regclass::text ~* '(crm|retail)' OR c.conrelid::regclass::text ~* '(crm|retail)')
 ORDER BY 3, 2;
-- Q5 tables of meta, etl, dq, app, audit with a column that ties rows to a source, an object, a job or a rule
SELECT 'Q5' AS q, table_schema || '.' || table_name AS table_name, string_agg(column_name, ', ' ORDER BY column_name) AS columns
  FROM information_schema.columns
 WHERE table_schema IN ('meta', 'etl', 'dq', 'app', 'audit')
   AND column_name IN ('source_system_code', 'source_system_id', 'source_object_id', 'job_id', 'job_code', 'rule_code',
                       'task_code', 'pipeline_code', 'batch_run_id', 'source_batch_run_id', 'object_name', 'object_ids')
 GROUP BY 1, 2 ORDER BY 2;
-- Q6 sources
SELECT 'Q6' AS q, CASE WHEN to_regclass('meta.source_system') IS NULL THEN 'ABSENT' WHEN has_table_privilege('meta.source_system', 'SELECT')
       THEN query_to_xml('SELECT * FROM meta.source_system ORDER BY 1', false, true, '')::text ELSE 'NO_SELECT' END;
-- Q7 objects of CRM_RETAIL
SELECT 'Q7' AS q, CASE WHEN to_regclass('meta.source_object') IS NULL THEN 'ABSENT' WHEN has_table_privilege('meta.source_object', 'SELECT')
       THEN query_to_xml('SELECT o.source_object_id, o.source_schema, o.source_object, o.is_active
                            FROM meta.source_object o JOIN meta.source_system s ON s.source_system_id = o.source_system_id
                           WHERE s.code = ''CRM_RETAIL'' ORDER BY 1', false, true, '')::text ELSE 'NO_SELECT' END;
-- Q8 contracts and mapping rules of CRM (the legacy marker M7)
SELECT 'Q8' AS q,
       CASE WHEN to_regclass('meta.column_contract') IS NULL THEN 'ABSENT' WHEN has_table_privilege('meta.column_contract', 'SELECT')
            THEN query_to_xml('SELECT source_system_code, count(*) AS n FROM meta.column_contract GROUP BY 1', false, true, '')::text
            ELSE 'NO_SELECT' END AS column_contract,
       CASE WHEN to_regclass('meta.mapping_rule') IS NULL THEN 'ABSENT' WHEN has_table_privilege('meta.mapping_rule', 'SELECT')
            THEN query_to_xml('SELECT source_system_code, count(*) AS n FROM meta.mapping_rule GROUP BY 1', false, true, '')::text
            ELSE 'NO_SELECT' END AS mapping_rule;
-- Q9 ETL jobs
SELECT 'Q9' AS q, CASE WHEN to_regclass('etl.job') IS NULL THEN 'ABSENT' WHEN has_table_privilege('etl.job', 'SELECT')
       THEN query_to_xml('SELECT * FROM etl.job ORDER BY 1', false, true, '')::text ELSE 'NO_SELECT' END;
-- Q10 DQ rules
SELECT 'Q10' AS q, CASE WHEN to_regclass('dq.v_current_rule') IS NULL THEN 'ABSENT' WHEN has_table_privilege('dq.v_current_rule', 'SELECT')
       THEN query_to_xml('SELECT * FROM dq.v_current_rule ORDER BY 1', false, true, '')::text ELSE 'NO_SELECT' END;
-- Q11 AI assistant: whitelist, examples and value columns per object
SELECT 'Q11' AS q, CASE WHEN to_regclass('meta.ai_report_object') IS NULL THEN 'ABSENT' WHEN has_table_privilege('meta.ai_report_object', 'SELECT')
       THEN query_to_xml('SELECT o.object_id, o.schema_name, o.object_name, o.is_active,
                                 (SELECT count(*) FROM meta.ai_example e WHERE e.object_id = o.object_id) AS examples,
                                 (SELECT count(*) FROM meta.ai_value_column v WHERE v.object_id = o.object_id) AS value_columns
                            FROM meta.ai_report_object o ORDER BY 1', false, true, '')::text ELSE 'NO_SELECT' END;
-- Q12 saved reports per object
SELECT 'Q12' AS q, CASE WHEN to_regclass('app.saved_report') IS NULL THEN 'ABSENT' WHEN has_table_privilege('app.saved_report', 'SELECT')
       THEN query_to_xml('SELECT unnest(object_ids) AS object_id, count(*) AS reports FROM app.saved_report GROUP BY 1',
                         false, true, '')::text ELSE 'NO_SELECT' END;
-- Q13 the old run queue: requests by task and status
SELECT 'Q13' AS q, CASE WHEN to_regclass('etl.run_request') IS NULL THEN 'ABSENT' WHEN has_table_privilege('etl.run_request', 'SELECT')
       THEN query_to_xml('SELECT * FROM etl.run_request ORDER BY 1 DESC LIMIT 5', false, true, '')::text ELSE 'NO_SELECT' END;
-- Q14 marts of the builder (none should be built on the old CRM ODS)
SELECT 'Q14' AS q, CASE WHEN to_regclass('meta.dm_mart') IS NULL THEN 'ABSENT' WHEN has_table_privilege('meta.dm_mart', 'SELECT')
       THEN query_to_xml('SELECT * FROM meta.dm_mart ORDER BY 1', false, true, '')::text ELSE 'NO_SELECT' END;
-- Q15 roles other than the owner granted something on the relations of Q1 (who loses access)
SELECT 'Q15' AS q, n.nspname || '.' || c.relname AS relation,
       string_agg(DISTINCT a.grantee::regrole::text || ':' || a.privilege_type, ', ') AS grants
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace CROSS JOIN LATERAL aclexplode(c.relacl) a
 WHERE c.relname ~* '(crm|retail)' AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND a.grantee <> c.relowner
 GROUP BY 2 ORDER BY 2
