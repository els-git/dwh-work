-- I02-04 M-SS04 check: read-only catalog verification of secret_store after
-- 001-004. Supersedes the 003 check once 004 is applied. Plain SQL; SELECT
-- only; reads pg_catalog, never row contents.
-- Expected: 23 results, every one PASS.
SELECT current_setting('transaction_read_only') AS transaction_read_only,
       current_database() AS database_name,
       current_user AS role_name,
       current_setting('server_version') AS server_version;

WITH s AS (SELECT oid, nspowner, nspacl FROM pg_namespace WHERE nspname = 'secret_store'),
t AS (SELECT c.oid, c.relname FROM pg_class c JOIN s ON c.relnamespace = s.oid WHERE c.relkind = 'r'),
f AS (SELECT p.oid, p.proname, p.prosecdef FROM pg_proc p JOIN s ON p.pronamespace = s.oid),
api AS (SELECT * FROM f WHERE proname IN ('begin_operation', 'record_denied_attempt', 'register_pending',
                                          'finish_operation', 'activate_binding')),
b AS (SELECT oid FROM t WHERE relname = 'binding'),
e AS (SELECT oid FROM t WHERE relname = 'operation_event'),
checks AS (
  SELECT 1 AS n, 'schema exists' AS check_name, (SELECT count(*) FROM s) = 1 AS ok
  UNION ALL SELECT 2, 'tables binding, operation_event',
    coalesce((SELECT array_agg(relname ORDER BY relname) FROM t) = ARRAY['binding', 'operation_event']::name[], false)
  UNION ALL SELECT 3, 'binding.profile_code text, nullable',
    EXISTS (SELECT 1 FROM pg_attribute a, b WHERE a.attrelid = b.oid AND a.attname = 'profile_code'
              AND NOT a.attisdropped AND NOT a.attnotnull AND a.atttypid = 'text'::regtype)
  UNION ALL SELECT 4, 'binding constraints = 10',
    (SELECT count(*) FROM pg_constraint WHERE conrelid = (SELECT oid FROM b)) = 10
  UNION ALL SELECT 5, 'operation_event constraints = 11',
    (SELECT count(*) FROM pg_constraint WHERE conrelid = (SELECT oid FROM e)) = 11
  UNION ALL SELECT 6, 'named constraints present',
    (SELECT count(*) FROM pg_constraint WHERE connamespace = (SELECT oid FROM s) AND conname IN (
      'ciphertext_digest_format', 'binding_state_timestamps', 'operation_code_by_phase',
      'operation_once_per_phase',
      'binding_profile_code_format', 'binding_active_requires_profile',
      'operation_type_valid', 'operation_correlation_by_type', 'replaced_only_for_replace',
      'actor_kind_valid', 'actor_ref_format', 'actor_required',
      'binding_key_id_format', 'binding_active_requires_key')) = 14
    AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE connamespace = (SELECT oid FROM s)
                     AND conname = 'event_for_create_operation')
  UNION ALL SELECT 16, 'operation_event: operation_type NOT NULL, replaced/actor columns',
    EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = (SELECT oid FROM e) AND attname = 'operation_type'
              AND attnotnull AND NOT attisdropped AND atttypid = 'text'::regtype)
    AND (SELECT count(*) FROM pg_attribute WHERE attrelid = (SELECT oid FROM e) AND NOT attisdropped
           AND attname IN ('replaced_create_operation_id', 'actor_kind', 'actor_ref') AND NOT attnotnull) = 3
    AND NOT EXISTS (SELECT 1 FROM pg_attrdef d JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
                     WHERE d.adrelid = (SELECT oid FROM e) AND a.attname = 'operation_type')
  UNION ALL SELECT 17, 'binding.key_id text, nullable',
    EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = (SELECT oid FROM b) AND attname = 'key_id'
              AND NOT attnotnull AND NOT attisdropped AND atttypid = 'text'::regtype)
  UNION ALL SELECT 18, 'NOT VALID: actor_required, binding_active_requires_key',
    (SELECT count(*) FROM pg_constraint WHERE connamespace = (SELECT oid FROM s) AND NOT convalidated
       AND conname IN ('actor_required', 'binding_active_requires_key')) = 2
  UNION ALL SELECT 19, 'operation_event_target_idx present',
    EXISTS (SELECT 1 FROM pg_class WHERE relnamespace = (SELECT oid FROM s) AND relkind = 'i'
              AND relname = 'operation_event_target_idx')
  UNION ALL SELECT 20, 'validate_event_insert is the 003 version',
    EXISTS (SELECT 1 FROM f JOIN pg_proc p ON p.oid = f.oid
             WHERE p.proname = 'validate_event_insert' AND p.prosrc LIKE '%READ requires an ACTIVE binding with a profile%')
  UNION ALL SELECT 7, 'ACTIVE requires profile: NOT VALID (legacy rows kept)',
    EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = (SELECT oid FROM b)
             AND conname = 'binding_active_requires_profile' AND contype = 'c' AND NOT convalidated)
  UNION ALL SELECT 8, 'one ACTIVE per profile: unique partial index',
    EXISTS (SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
             WHERE c.relname = 'binding_one_active_per_profile' AND i.indrelid = (SELECT oid FROM b)
               AND i.indisunique AND i.indisvalid AND i.indpred IS NOT NULL)
  UNION ALL SELECT 9, 'baseline indexes present',
    (SELECT count(*) FROM pg_class WHERE relnamespace = (SELECT oid FROM s) AND relkind = 'i'
       AND relname IN ('binding_source_state_idx', 'operation_event_source_created_idx')) = 2
  UNION ALL SELECT 10, '8 guard triggers enabled (incl. 2 TRUNCATE, lifecycle)',
    (SELECT count(*) FROM pg_trigger WHERE tgrelid IN (SELECT oid FROM t) AND NOT tgisinternal AND tgenabled = 'O'
       AND tgname IN ('binding_guard', 'event_append_only', 'event_insert_guard', 'binding_insert_guard',
                      'binding_truncate_guard', 'event_truncate_guard', 'binding_profile_guard',
                      'binding_lifecycle_guard')) = 8
  UNION ALL SELECT 11, 'TRUNCATE triggers fire before statement',
    (SELECT count(*) FROM pg_trigger WHERE tgrelid IN (SELECT oid FROM t) AND NOT tgisinternal
       AND tgname IN ('binding_truncate_guard', 'event_truncate_guard')
       AND (tgtype & 32) <> 0 AND (tgtype & 2) <> 0 AND (tgtype & 1) = 0) = 2
  UNION ALL SELECT 12, '12 functions; only the 5 write functions SECURITY DEFINER',
    (SELECT count(*) FROM f) = 12 AND (SELECT count(*) FROM api) = 5
    AND NOT EXISTS (SELECT 1 FROM f WHERE prosecdef AND oid NOT IN (SELECT oid FROM api))
  UNION ALL SELECT 21, 'write functions: SECURITY DEFINER with fixed search_path',
    (SELECT count(*) FROM api JOIN pg_proc p ON p.oid = api.oid
      WHERE p.prosecdef AND p.proconfig @> ARRAY['search_path=pg_catalog, secret_store']) = 5
  UNION ALL SELECT 22, 'write functions owned by the schema owner',
    NOT EXISTS (SELECT 1 FROM api JOIN pg_proc p ON p.oid = api.oid, s WHERE p.proowner <> s.nspowner)
  UNION ALL SELECT 23, 'write functions: EXECUTE granted to nobody',
    (SELECT count(*) FROM api JOIN pg_proc p ON p.oid = api.oid WHERE p.proacl IS NOT NULL) = 5
    AND NOT EXISTS (SELECT 1 FROM api JOIN pg_proc p ON p.oid = api.oid, aclexplode(p.proacl) x
                     WHERE x.grantee <> p.proowner)
  UNION ALL SELECT 13, 'schema: no grants beyond owner',
    (SELECT count(*) FROM s) = 1 AND NOT EXISTS (
      SELECT 1 FROM s, aclexplode(coalesce(s.nspacl, acldefault('n', s.nspowner))) a WHERE a.grantee <> s.nspowner)
  UNION ALL SELECT 14, 'tables: no grants beyond owner',
    (SELECT count(*) FROM t) = 2 AND NOT EXISTS (
      SELECT 1 FROM t JOIN pg_class c ON c.oid = t.oid, aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a
       WHERE a.grantee <> c.relowner)
  UNION ALL SELECT 15, 'functions: no PUBLIC or other EXECUTE',
    (SELECT count(*) FROM f) = 12 AND NOT EXISTS (
      SELECT 1 FROM f JOIN pg_proc p ON p.oid = f.oid, aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
       WHERE a.grantee <> p.proowner)
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks ORDER BY n;

-- Row counts only (no contents). NULL = table absent or no SELECT privilege
-- for the current role (expected for dwh_app: no grants by design).
SELECT r.relname AS table_name,
       CASE WHEN c.oid IS NOT NULL THEN
         CASE WHEN has_schema_privilege(c.relnamespace, 'USAGE') AND has_table_privilege(c.oid, 'SELECT') THEN
           (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM secret_store.%I', r.relname), false, true, '')))[1]::text::bigint
         END
       END AS row_count
  FROM (VALUES ('binding'), ('operation_event')) AS r(relname)
  LEFT JOIN pg_class c
    ON c.relname = r.relname AND c.relkind = 'r'
   AND c.relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'secret_store');
