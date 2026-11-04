-- I05 check of 009: stg.operation_collector_asan_pension_requests and its applied proposal.
-- SELECT only; pg_catalog and meta.target_change_proposal (owner). Run as the owner
-- with default_transaction_read_only = on. Expected: 8 results, every one PASS.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name, current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH t AS (SELECT c.oid, c.relacl, c.relowner, c.relkind, n.nspowner FROM pg_class c
             JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'stg' AND c.relname = 'operation_collector_asan_pension_requests'),
checks AS (
  SELECT 1 AS n, 'table stg.operation_collector_asan_pension_requests present' AS check_name,
    (SELECT count(*) FROM t WHERE relkind = 'r') = 1 AS ok
  UNION ALL SELECT 2, '17 columns with the proposed types, in order',
    (SELECT string_agg(a.attname || ':' || format_type(a.atttypid, a.atttypmod), '|' ORDER BY a.attnum)
       FROM pg_attribute a WHERE a.attrelid = (SELECT oid FROM t) AND a.attnum > 0 AND NOT a.attisdropped)
    = 'id:bigint|pin:character varying(16)|name:character varying(256)|surname:character varying(256)|meta_data:jsonb|patronymic:character varying(256)|birth_date:character varying(16)|is_success:boolean|is_cache:boolean|has_meta_data:boolean|is_paid:boolean|asan_response_status:character varying(32)|message:text|interval_days:bigint|channel:character varying(64)|product_type:character varying(64)|created_at:timestamp without time zone'
  UNION ALL SELECT 3, 'primary key pk_operation_collector_asan_pension_requests (id); only id NOT NULL',
    EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conrelid = (SELECT oid FROM t) AND k.contype = 'p'
             AND k.conname = 'pk_operation_collector_asan_pension_requests'
             AND (SELECT array_agg(a.attname::text) FROM pg_attribute a
                   WHERE a.attrelid = k.conrelid AND a.attnum = ANY(k.conkey)) = ARRAY['id'])
    AND (SELECT count(*) FROM pg_attribute a WHERE a.attrelid = (SELECT oid FROM t) AND a.attnum > 0 AND a.attnotnull) = 1
  UNION ALL SELECT 4, 'owner is the owner of schema stg',
    (SELECT relowner = nspowner FROM t)
  UNION ALL SELECT 5, 'dwh_etl may SELECT, INSERT, UPDATE, DELETE (default privileges of stg)',
    (SELECT has_table_privilege('dwh_etl', oid, 'SELECT') AND has_table_privilege('dwh_etl', oid, 'INSERT')
        AND has_table_privilege('dwh_etl', oid, 'UPDATE') AND has_table_privilege('dwh_etl', oid, 'DELETE') FROM t)
  UNION ALL SELECT 6, 'dwh_app and PUBLIC have no privileges',
    (SELECT NOT has_table_privilege('dwh_app', oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER') FROM t)
    AND NOT EXISTS (SELECT 1 FROM t, aclexplode(coalesce(t.relacl, acldefault('r', t.relowner))) x WHERE x.grantee = 0)
  UNION ALL SELECT 7, 'reviewed proposal is APPLIED (DDL SHA-256 matches)',
    (SELECT count(*) FROM meta.target_change_proposal
      WHERE target_schema = 'stg' AND target_table = 'operation_collector_asan_pension_requests'
        AND change_kind = 'CREATE_TABLE' AND status = 'APPLIED'
        AND encode(sha256(convert_to(ddl, 'UTF8')), 'hex') = 'b964a1bcd172ee4136728d1350b2ba78c769b5e6e2e0eff5d238dd0f321850ae') = 1
  UNION ALL SELECT 8, 'no open proposal left for the table',
    NOT EXISTS (SELECT 1 FROM meta.target_change_proposal
                 WHERE target_schema = 'stg' AND target_table = 'operation_collector_asan_pension_requests'
                   AND status = 'PROPOSED')
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;
