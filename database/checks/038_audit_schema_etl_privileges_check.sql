-- I14-07 check of 038: read-only catalog verification, runs as dwh_app. Expected: 6 results, every one PASS.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH rel AS (SELECT c.oid, c.relkind FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace
              WHERE s.nspname = 'audit' AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')),
checks AS (
  SELECT 1 AS n, 'audit.revoked_grant_038 present' AS check_name,
    EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace
             WHERE s.nspname = 'audit' AND c.relname = 'revoked_grant_038') AS ok
  UNION ALL SELECT 2, 'dwh_etl has no privilege on tables and views of schema audit',
    NOT EXISTS (SELECT 1 FROM rel WHERE relkind <> 'S'
                   AND has_table_privilege('dwh_etl', rel.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'))
  UNION ALL SELECT 3, 'dwh_etl has no privilege on sequences of schema audit',
    NOT EXISTS (SELECT 1 FROM rel WHERE CASE WHEN relkind = 'S'
                 THEN has_sequence_privilege('dwh_etl', rel.oid, 'SELECT,UPDATE,USAGE') ELSE false END)
  UNION ALL SELECT 4, 'no default privileges of schema audit for dwh_etl',
    NOT EXISTS (SELECT 1 FROM pg_default_acl d JOIN pg_namespace s ON s.oid = d.defaclnamespace AND s.nspname = 'audit',
                       aclexplode(d.defaclacl) a WHERE a.grantee = 'dwh_etl'::regrole)
  UNION ALL SELECT 5, 'dwh_app (this session) cannot read audit.revoked_grant_038',
    NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace
                 WHERE s.nspname = 'audit' AND c.relname = 'revoked_grant_038' AND has_table_privilege(c.oid, 'SELECT'))
  UNION ALL SELECT 6, 'loads unchanged: dwh_etl may still call etl.object_run_begin',
    has_function_privilege('dwh_etl', 'etl.object_run_begin(bigint)', 'EXECUTE')
)
SELECT n, check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
