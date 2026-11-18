-- I23 rollback of 023: the 018 functions get their previous text back, the new functions, the columns of
-- meta.dm_mart_version and the grants of dwh_dm_sql_ro are removed. Refuses and changes nothing if any mart
-- version is a SQL version. The role dwh_dm_sql_ro itself is kept (roles are shared by the databases of the
-- server); it loses every grant of this database. Run as the owner (dwh_admin):
--   psql -v ON_ERROR_STOP=1 -f 023_sql_mart.down.sql
BEGIN;
LOCK TABLE meta.dm_mart_version IN ACCESS EXCLUSIVE MODE;
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM meta.dm_mart_version WHERE version_kind = 'SQL') THEN
    RAISE EXCEPTION 'ROLLBACK REFUSED: SQL versions of marts exist';
  END IF;
END
$do$;

DO $patch$
DECLARE
  r record;
  v_def text;
  v_pos integer;
BEGIN
  FOR r IN
    SELECT * FROM (VALUES
      (1, 'meta.dm_mart_version_problems(bigint)',
       E'  IF v.version_kind = ''SQL'' THEN RETURN meta.sql_mart_problems(p_version_id); END IF;  -- I23 (023)\n', ''),
      (2, 'etl.dm_mart_enqueue_after_ods(text)',
       E'     AND (EXISTS (SELECT 1 FROM meta.dm_mart_source s WHERE s.version_id = v.version_id AND s.table_name = v_table)\n'
       || E'          OR v_table = ANY (coalesce(v.sql_tables, ARRAY[]::text[])))  -- I23 (023)\n',
       E'     AND EXISTS (SELECT 1 FROM meta.dm_mart_source s WHERE s.version_id = v.version_id AND s.table_name = v_table)\n'),
      (3, 'app.dm_mart_version_create(text, bigint)',
       'INSERT INTO meta.dm_mart_version (mart_id, version_no, grain_entity_id, created_by_user_id, version_kind, sql_text, sql_sha256, sql_tables)',
       'INSERT INTO meta.dm_mart_version (mart_id, version_no, grain_entity_id, created_by_user_id)'),
      (4, 'app.dm_mart_version_create(text, bigint)',
       E'          pub.grain_entity_id, v_actor, coalesce(pub.version_kind, ''BUILDER''), pub.sql_text, pub.sql_sha256, pub.sql_tables)',
       E'          pub.grain_entity_id, v_actor)'),
      (5, 'app.dm_mart_version_save(text, bigint, jsonb)',
       E'         version_kind = ''BUILDER'', sql_text = NULL, sql_sha256 = NULL, sql_tables = NULL,  -- I23 (023)\n', '')
    ) AS t(n, fn, old_text, new_text) ORDER BY n
  LOOP
    v_def := pg_get_functiondef(r.fn::regprocedure);
    v_pos := position(r.old_text IN v_def);
    IF v_pos = 0 OR position(r.old_text IN substr(v_def, v_pos + 1)) > 0 THEN
      RAISE EXCEPTION 'STOP: fragment % of % not found exactly once', r.n, r.fn;
    END IF;
    EXECUTE replace(v_def, r.old_text, r.new_text);
  END LOOP;
END
$patch$;

DROP FUNCTION app.sql_mart_save(text, bigint, text, text[], jsonb);
DROP FUNCTION meta.sql_mart_problems(bigint);
ALTER TABLE meta.dm_mart_version DROP CONSTRAINT dm_mart_version_sql_shape;
ALTER TABLE meta.dm_mart_version DROP COLUMN sql_tables, DROP COLUMN sql_sha256, DROP COLUMN sql_text,
  DROP COLUMN version_kind;

DO $odsdef$
DECLARE
  v_role text;
BEGIN
  FOR v_role IN
    SELECT DISTINCT x FROM unnest(ARRAY[current_user::text,
                                        (SELECT pg_get_userbyid(n.nspowner) FROM pg_namespace n WHERE n.nspname = 'ods')]) x
  LOOP
    IF pg_has_role(current_user, v_role, 'MEMBER') THEN
      EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA ods REVOKE SELECT ON TABLES FROM dwh_dm_sql_ro', v_role);
    END IF;
  END LOOP;
END
$odsdef$;
REVOKE ALL ON ALL TABLES IN SCHEMA ods FROM dwh_dm_sql_ro;
REVOKE ALL ON SCHEMA ods FROM dwh_dm_sql_ro;

SELECT 'ROLLBACK_023_DONE' AS result;
COMMIT;
