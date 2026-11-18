-- I23: DM mart from a ready SQL query -- package 023
-- (plan docs/DWH_I23_SQL_MART_PLAN_APPROVED_20261118.md, decisions C1-C8 of 18.11.2026).
--
-- C2  The query reads only tables of schema ods.
-- C3  It runs as the new role dwh_dm_sql_ro: SELECT on ods only (and new ods tables through default privileges),
--     read-only transactions, statement timeout. The role is created WITHOUT a password: it cannot log in
--     until the password is set on the server by the setup tool (as dwh_ai_ro, 016).
-- C4  One query: SELECT, WITH ... SELECT or a set operation (UNION / INTERSECT / EXCEPT). The API checks it
--     under dwh_dm_sql_ro (EXPLAIN VERBOSE: only ods tables) and passes the text, its tables and the result
--     columns to app.sql_mart_save; the database stores the text with its SHA-256.
-- C5  Columns of the mart are the fields of the version (meta.dm_mart_field, types of the 018 whitelist, exactly
--     one key); the DDL, the publication that builds aside (dm.<code>__next) and the swap of 019 work unchanged.
-- C7  A published SQL mart is refreshed after ODS of the tables of its query (etl.dm_mart_enqueue_after_ods).
-- C8  A new text is a new version.
-- Changes of 018 functions are made in place on their current text (pg_get_functiondef) and stop if the expected
-- fragment is not found: meta.dm_mart_version_problems (SQL versions are checked by meta.sql_mart_problems),
-- etl.dm_mart_enqueue_after_ods (tables of the query), app.dm_mart_version_create (a new draft copies the query),
-- app.dm_mart_version_save (a constructor save makes the draft a constructor version again). New functions are
-- named sql_mart_* (checks 018/019 count dm_* / mart_builder_* functions). Requires 019.
-- Run as the owner of schemas meta, etl, app (dwh_admin):   psql -1 -v ON_ERROR_STOP=1 -f 023_sql_mart.sql
DO $pre$
BEGIN
  SET LOCAL search_path = pg_catalog;
  IF to_regprocedure('app.mart_builder_publish(text, bigint, boolean)') IS NULL THEN
    RAISE EXCEPTION 'STOP: package 019 is not applied';
  END IF;
  IF to_regnamespace('ods') IS NULL THEN RAISE EXCEPTION 'STOP: schema ods not found'; END IF;
  IF EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = 'meta.dm_mart_version'::regclass
              AND attname = 'version_kind' AND NOT attisdropped) THEN
    RAISE EXCEPTION 'STOP: 023 is already applied';
  END IF;
  -- dwh_dm_sql_ro may already exist (roles are shared by the databases of the server, E1 creates it):
  -- then it must be a plain role.
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dwh_dm_sql_ro'
              AND (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls)) THEN
    RAISE EXCEPTION 'STOP: role dwh_dm_sql_ro exists with elevated attributes';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member WHERE r.rolname = 'dwh_dm_sql_ro') THEN
    RAISE EXCEPTION 'STOP: role dwh_dm_sql_ro is a member of other roles';
  END IF;
END
$pre$;

-- ------------------------------------------------------------------ version of a mart: kind and query

ALTER TABLE meta.dm_mart_version
  ADD COLUMN version_kind text NOT NULL DEFAULT 'BUILDER' CHECK (version_kind IN ('BUILDER', 'SQL')),
  ADD COLUMN sql_text text CHECK (char_length(btrim(sql_text)) BETWEEN 1 AND 50000),
  ADD COLUMN sql_sha256 text CHECK (sql_sha256 ~ '^[0-9a-f]{64}$'),
  ADD COLUMN sql_tables text[];
ALTER TABLE meta.dm_mart_version ADD CONSTRAINT dm_mart_version_sql_shape CHECK (
  (version_kind = 'SQL') = (sql_text IS NOT NULL)
  AND (sql_text IS NULL) = (sql_sha256 IS NULL)
  AND (sql_text IS NULL) = (sql_tables IS NULL)
  AND (sql_tables IS NULL OR cardinality(sql_tables) BETWEEN 1 AND 50));

-- ------------------------------------------------------------------ functions

-- Problems of a SQL version (empty = valid): the query, its tables, the fields; no constructor parts.
CREATE FUNCTION meta.sql_mart_problems(p_version_id bigint) RETURNS text[]
LANGUAGE plpgsql STABLE SET search_path = pg_catalog, meta AS $fn$
DECLARE
  v meta.dm_mart_version;
  p text[] := ARRAY[]::text[];
  t text;
  v_keys integer;
BEGIN
  SELECT * INTO v FROM meta.dm_mart_version x WHERE x.version_id = p_version_id;
  IF v.sql_text IS NULL THEN RETURN ARRAY['SQL text is missing']; END IF;
  IF v.sql_sha256 <> encode(sha256(convert_to(v.sql_text, 'UTF8')), 'hex') THEN
    p := p || 'SQL text does not match its SHA-256'::text;
  END IF;
  FOREACH t IN ARRAY v.sql_tables LOOP
    IF t !~ '^[a-z_][a-z0-9_]{0,62}$' OR to_regclass(format('ods.%I', t)) IS NULL THEN
      p := p || format('table ods.%s not found', t);
    END IF;
  END LOOP;
  IF EXISTS (SELECT 1 FROM meta.dm_mart_source x WHERE x.version_id = p_version_id)
     OR EXISTS (SELECT 1 FROM meta.dm_mart_filter x WHERE x.version_id = p_version_id)
     OR EXISTS (SELECT 1 FROM meta.dm_mart_field d JOIN meta.dm_mart_field_source x ON x.field_id = d.field_id
                 WHERE d.version_id = p_version_id) THEN
    p := p || 'a SQL version has no sources, field sources or filters'::text;
  END IF;
  SELECT count(*) INTO v_keys FROM meta.dm_mart_field x WHERE x.version_id = p_version_id AND x.is_key;
  IF v_keys <> 1 THEN p := p || 'exactly one key field is required'::text; END IF;
  IF NOT EXISTS (SELECT 1 FROM meta.dm_mart_field x WHERE x.version_id = p_version_id AND NOT x.is_key) THEN
    p := p || 'at least one field besides the key is required'::text;
  END IF;
  RETURN p;
END;
$fn$;

-- Saves the query of a DRAFT version (C4, C5): the text, the ods tables of its plan and the result columns as
-- fields [{"name": "client_id", "title": "...", "type": "bigint", "is_key": true}, ...]. The API has checked the
-- query under dwh_dm_sql_ro; constructor parts of the draft are removed. Returns the problems of the version.
CREATE FUNCTION app.sql_mart_save(p_token_hash text, p_version_id bigint, p_sql_text text, p_sql_tables text[],
                                  p_fields jsonb)
RETURNS text[]
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta, audit AS $fn$
DECLARE
  v_actor bigint := app.integration_admin_session_user(p_token_hash);
  v meta.dm_mart_version;
  v_sql text := btrim(coalesce(p_sql_text, ''));
  v_tables text[];
  e record;
BEGIN
  SELECT * INTO v FROM meta.dm_mart_version y WHERE y.version_id = p_version_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Mart version not found'; END IF;
  IF v.status <> 'DRAFT' THEN RAISE EXCEPTION 'Only a DRAFT version can be changed'; END IF;
  IF char_length(v_sql) NOT BETWEEN 1 AND 50000 THEN RAISE EXCEPTION 'SQL text must be 1-50000 characters'; END IF;
  SELECT array_agg(DISTINCT t ORDER BY t) INTO v_tables FROM unnest(coalesce(p_sql_tables, ARRAY[]::text[])) t;
  IF coalesce(cardinality(v_tables), 0) NOT BETWEEN 1 AND 50
     OR EXISTS (SELECT 1 FROM unnest(v_tables) t WHERE t IS NULL OR t !~ '^[a-z_][a-z0-9_]{0,62}$') THEN
    RAISE EXCEPTION 'The query must read 1-50 tables of schema ods';
  END IF;
  IF p_fields IS NULL OR jsonb_typeof(p_fields) <> 'array' OR jsonb_array_length(p_fields) NOT BETWEEN 1 AND 300 THEN
    RAISE EXCEPTION 'Invalid fields of the query';
  END IF;
  DELETE FROM meta.dm_mart_filter y WHERE y.version_id = p_version_id;
  DELETE FROM meta.dm_mart_field y WHERE y.version_id = p_version_id;
  DELETE FROM meta.dm_mart_source y WHERE y.version_id = p_version_id;
  UPDATE meta.dm_mart_version y SET version_kind = 'SQL', sql_text = v_sql, sql_tables = v_tables,
         sql_sha256 = encode(sha256(convert_to(v_sql, 'UTF8')), 'hex'), grain_entity_id = NULL,
         updated_at = clock_timestamp(),
         ddl_status = CASE WHEN y.ddl_status = 'PROPOSED' THEN 'NONE' ELSE y.ddl_status END,
         ddl_text = CASE WHEN y.ddl_status = 'PROPOSED' THEN NULL ELSE y.ddl_text END,
         ddl_sha256 = CASE WHEN y.ddl_status = 'PROPOSED' THEN NULL ELSE y.ddl_sha256 END
   WHERE y.version_id = p_version_id;
  FOR e IN SELECT value AS j, ordinality AS n FROM jsonb_array_elements(p_fields) WITH ORDINALITY LOOP
    INSERT INTO meta.dm_mart_field (version_id, position, field_name, title, data_type, is_key)
    VALUES (p_version_id, e.n, e.j->>'name', nullif(btrim(coalesce(e.j->>'title', '')), ''), lower(e.j->>'type'),
            coalesce((e.j->>'is_key')::boolean, false));
  END LOOP;
  PERFORM audit.log_security_event('DM_MART_SQL_VERSION_SAVED', 'DM_MART', v.mart_id::text, 'SAVE', 'SUCCESS',
    jsonb_build_object('actor_user_id', v_actor, 'version_id', p_version_id, 'tables', to_jsonb(v_tables),
                       'sql_sha256', encode(sha256(convert_to(v_sql, 'UTF8')), 'hex')));
  RETURN meta.dm_mart_version_problems(p_version_id);
END;
$fn$;

-- ------------------------------------------------------------------ changes of 018 functions, in place

DO $patch$
DECLARE
  r record;
  v_def text;
  v_pos integer;
BEGIN
  FOR r IN
    SELECT * FROM (VALUES
      (1, 'meta.dm_mart_version_problems(bigint)',
       E'  SELECT * INTO v FROM meta.dm_mart_version x WHERE x.version_id = p_version_id;\n  IF v.grain_entity_id IS NULL',
       E'  SELECT * INTO v FROM meta.dm_mart_version x WHERE x.version_id = p_version_id;\n'
       || E'  IF v.version_kind = ''SQL'' THEN RETURN meta.sql_mart_problems(p_version_id); END IF;  -- I23 (023)\n'
       || E'  IF v.grain_entity_id IS NULL'),
      (2, 'etl.dm_mart_enqueue_after_ods(text)',
       E'     AND EXISTS (SELECT 1 FROM meta.dm_mart_source s WHERE s.version_id = v.version_id AND s.table_name = v_table)\n',
       E'     AND (EXISTS (SELECT 1 FROM meta.dm_mart_source s WHERE s.version_id = v.version_id AND s.table_name = v_table)\n'
       || E'          OR v_table = ANY (coalesce(v.sql_tables, ARRAY[]::text[])))  -- I23 (023)\n'),
      (3, 'app.dm_mart_version_create(text, bigint)',
       'INSERT INTO meta.dm_mart_version (mart_id, version_no, grain_entity_id, created_by_user_id)',
       'INSERT INTO meta.dm_mart_version (mart_id, version_no, grain_entity_id, created_by_user_id, version_kind, sql_text, sql_sha256, sql_tables)'),
      (4, 'app.dm_mart_version_create(text, bigint)',
       E'          pub.grain_entity_id, v_actor)',
       E'          pub.grain_entity_id, v_actor, coalesce(pub.version_kind, ''BUILDER''), pub.sql_text, pub.sql_sha256, pub.sql_tables)'),
      (5, 'app.dm_mart_version_save(text, bigint, jsonb)',
       E'  UPDATE meta.dm_mart_version y SET grain_entity_id = (d->>''grain_entity_id'')::bigint, updated_at = clock_timestamp(),\n',
       E'  UPDATE meta.dm_mart_version y SET grain_entity_id = (d->>''grain_entity_id'')::bigint, updated_at = clock_timestamp(),\n'
       || E'         version_kind = ''BUILDER'', sql_text = NULL, sql_sha256 = NULL, sql_tables = NULL,  -- I23 (023)\n')
    ) AS t(n, fn, old_text, new_text) ORDER BY n
  LOOP
    v_def := pg_get_functiondef(r.fn::regprocedure);
    v_pos := position(r.old_text IN v_def);
    IF v_pos = 0 OR position(r.old_text IN substr(v_def, v_pos + 1)) > 0 THEN
      RAISE EXCEPTION 'STOP: fragment % of % not found exactly once', r.n, r.fn;
    END IF;
    EXECUTE replace(v_def, r.old_text, r.new_text);
    RAISE NOTICE 'FUNCTION_CHANGED % (fragment %)', r.fn, r.n;
  END LOOP;
END
$patch$;

-- ------------------------------------------------------------------ role dwh_dm_sql_ro (C3)

DO $role$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dwh_dm_sql_ro') THEN
    CREATE ROLE dwh_dm_sql_ro LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS
      CONNECTION LIMIT 5;
    RAISE NOTICE 'ROLE_CREATED dwh_dm_sql_ro (no password: login impossible until it is set on the server)';
  ELSE
    ALTER ROLE dwh_dm_sql_ro LOGIN NOINHERIT CONNECTION LIMIT 5;
    RAISE NOTICE 'ROLE_EXISTS dwh_dm_sql_ro';
  END IF;
END
$role$;
ALTER ROLE dwh_dm_sql_ro SET default_transaction_read_only = on;
ALTER ROLE dwh_dm_sql_ro SET statement_timeout = '30min';
ALTER ROLE dwh_dm_sql_ro SET idle_in_transaction_session_timeout = '10min';
ALTER ROLE dwh_dm_sql_ro SET search_path = ods;

GRANT USAGE ON SCHEMA ods TO dwh_dm_sql_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA ods TO dwh_dm_sql_ro;
-- New ods tables are created by the owner of the publication functions (021) and by the owner of the schema.
DO $odsdef$
DECLARE
  v_role text;
BEGIN
  FOR v_role IN
    SELECT DISTINCT x FROM unnest(ARRAY[current_user::text,
                                        (SELECT pg_get_userbyid(n.nspowner) FROM pg_namespace n WHERE n.nspname = 'ods')]) x
  LOOP
    IF pg_has_role(current_user, v_role, 'MEMBER') THEN
      EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA ods GRANT SELECT ON TABLES TO dwh_dm_sql_ro', v_role);
      RAISE NOTICE 'DEFAULT_SELECT_ON_ODS for tables of %', v_role;
    ELSE
      RAISE NOTICE 'DEFAULT_SELECT_ON_ODS skipped for % (not a member)', v_role;
    END IF;
  END LOOP;
END
$odsdef$;

-- ------------------------------------------------------------------ privileges

REVOKE ALL ON FUNCTION meta.sql_mart_problems(bigint), app.sql_mart_save(text, bigint, text, text[], jsonb) FROM PUBLIC;
-- Default privileges of schemas meta/app (rule since 005): remove every grant on the new functions except the owner's.
DO $acl$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT 'FUNCTION ' || p.oid::regprocedure::text AS obj, x.grantee
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace, aclexplode(p.proacl) x
     WHERE n.nspname IN ('meta', 'app') AND p.proname LIKE 'sql\_mart\_%' AND x.grantee <> p.proowner
  LOOP
    EXECUTE format('REVOKE ALL ON %s FROM %s', r.obj,
                   CASE WHEN r.grantee = 0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    RAISE NOTICE 'DEFAULT_GRANT_REVOKED % FROM %', r.obj,
                 CASE WHEN r.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(r.grantee) END;
  END LOOP;
END
$acl$;
GRANT EXECUTE ON FUNCTION app.sql_mart_save(text, bigint, text, text[], jsonb) TO dwh_app;

SELECT 'MIGRATION_023_DONE' AS result;
