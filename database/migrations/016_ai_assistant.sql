-- I17 (step 3): local AI assistant -- package 016
-- (plan docs/DWH_I17_LOCAL_AI_ASSISTANT_PLAN_APPROVED_20261113.md, decisions L1-L9; U1-U4 of 13.11.2026).
--
-- U1  Three permissions: AI_ASSISTANT_USE (chat and help), AI_REPORT_RUN (report from a description),
--     AI_ADMIN_MANAGE (whitelist, dictionary, examples, request log). Group AI. SUPERADMIN/ADMIN get them
--     automatically through app.auth_get_permissions (as every active permission); other users through roles.
-- U2  meta.ai_value_column: reference columns whose distinct values the assistant may show to the model.
-- U3  Role dwh_ai_ro: reads the whole schema dm and nothing else (read-only transactions, statement timeout).
--     What the model sees is narrowed by the whitelist meta.ai_report_object.
-- U4  dwh_ai_ro is created WITHOUT a password: it cannot log in until the password is set on the server
--     (step 8, /etc/dwh/dwh.env). No password is in git.
-- Role-aware access (requirement of 13.11.2026): every whitelist object names the permission a user needs
-- (required_permission_code). app.ai_object_access(token) returns the full description only of objects the
-- session user may see; of the others only the title, so that the assistant can answer "not available to
-- your role" without seeing their columns. The SQL validator of the backend uses the same list.
-- app.ai_request_log keeps the question, the SQL, the result status and counters -- never result data,
-- never the image itself (only the flag that one was attached).
-- Nothing else changes: functions and tables of 001-015 stay as they are.
-- Requires app.auth_get_permissions(bigint), app.auth_session, app.user_account, app.permission,
-- permission REPORT_VIEW. Run as the owner of schemas app, meta, dm (dwh_admin):
--   psql -1 -v ON_ERROR_STOP=1 -f 016_ai_assistant.sql
DO $pre$
BEGIN
  SET LOCAL search_path = pg_catalog;
  IF to_regprocedure('app.auth_get_permissions(bigint)') IS NULL THEN
    RAISE EXCEPTION 'STOP: app.auth_get_permissions(bigint) not found';
  END IF;
  IF to_regclass('app.auth_session') IS NULL OR to_regclass('app.user_account') IS NULL
     OR to_regclass('app.permission') IS NULL THEN
    RAISE EXCEPTION 'STOP: app.auth_session, app.user_account or app.permission not found';
  END IF;
  IF to_regnamespace('dm') IS NULL THEN RAISE EXCEPTION 'STOP: schema dm not found'; END IF;
  IF to_regclass('meta.ai_report_object') IS NOT NULL THEN RAISE EXCEPTION 'STOP: 016 is already applied'; END IF;
  IF EXISTS (SELECT 1 FROM app.permission
              WHERE permission_code IN ('AI_ASSISTANT_USE', 'AI_REPORT_RUN', 'AI_ADMIN_MANAGE')) THEN
    RAISE EXCEPTION 'STOP: AI_* permissions already exist';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM app.permission WHERE permission_code = 'REPORT_VIEW' AND is_active) THEN
    RAISE EXCEPTION 'STOP: permission REPORT_VIEW not found';
  END IF;
  IF to_regclass('dm.v_retail_application_current') IS NULL THEN
    RAISE EXCEPTION 'STOP: dm.v_retail_application_current not found';
  END IF;
  -- dwh_ai_ro may already exist (roles are shared by the databases of the server, E1 creates it):
  -- then it must be a plain role.
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dwh_ai_ro'
              AND (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls)) THEN
    RAISE EXCEPTION 'STOP: role dwh_ai_ro exists with elevated attributes';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member WHERE r.rolname = 'dwh_ai_ro') THEN
    RAISE EXCEPTION 'STOP: role dwh_ai_ro is a member of other roles';
  END IF;
END
$pre$;

-- ------------------------------------------------------------------ permissions (U1)

DO $perm$
DECLARE
  a record;
  v_auto boolean;
  v_next bigint;
BEGIN
  SET LOCAL search_path = pg_catalog;
  LOCK TABLE app.permission IN SHARE ROW EXCLUSIVE MODE;
  SELECT att.attidentity <> '' OR EXISTS (SELECT 1 FROM pg_attrdef d WHERE d.adrelid = att.attrelid AND d.adnum = att.attnum)
    INTO v_auto
    FROM pg_attribute att WHERE att.attrelid = 'app.permission'::regclass AND att.attname = 'permission_id';
  SELECT coalesce(max(permission_id), 0) INTO v_next FROM app.permission;
  FOR a IN SELECT * FROM (VALUES
      (1, 'AI_ASSISTANT_USE', 'AI assistant: chat and help'),
      (2, 'AI_REPORT_RUN', 'AI assistant: report from a description'),
      (3, 'AI_ADMIN_MANAGE', 'AI assistant: administration')) v(n, code, name) ORDER BY n
  LOOP
    IF v_auto THEN
      INSERT INTO app.permission (permission_code, permission_name, permission_group, is_assignable, is_active)
      VALUES (a.code, a.name, 'AI', true, true);
    ELSE
      v_next := v_next + 1;
      INSERT INTO app.permission (permission_id, permission_code, permission_name, permission_group, is_assignable, is_active)
      VALUES (v_next, a.code, a.name, 'AI', true, true);
    END IF;
    RAISE NOTICE 'PERMISSION_ADDED %', a.code;
  END LOOP;
END
$perm$;

-- ------------------------------------------------------------------ configuration (meta)

CREATE TABLE meta.ai_report_object (
  object_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  schema_name text NOT NULL CHECK (schema_name = 'dm'),
  object_name text NOT NULL CHECK (object_name ~ '^[a-z_][a-z0-9_]{0,62}$'),
  title text NOT NULL CHECK (char_length(btrim(title)) BETWEEN 1 AND 200),
  description text CHECK (char_length(description) <= 4000),
  required_permission_code text NOT NULL CHECK (required_permission_code ~ '^[A-Z][A-Z0-9_]*$'),
  is_active boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_by_user_id bigint,
  CONSTRAINT ai_report_object_name UNIQUE (schema_name, object_name)
);

-- U2: reference columns whose distinct values the model may see.
CREATE TABLE meta.ai_value_column (
  value_column_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  object_id bigint NOT NULL REFERENCES meta.ai_report_object(object_id) ON DELETE RESTRICT,
  column_name text NOT NULL CHECK (column_name ~ '^[a-z_][a-z0-9_]{0,62}$'),
  max_values integer NOT NULL DEFAULT 50 CHECK (max_values BETWEEN 1 AND 500),
  is_active boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_by_user_id bigint,
  CONSTRAINT ai_value_column_name UNIQUE (object_id, column_name)
);

-- Terms of the business: explained to the model (general, or bound to one object).
CREATE TABLE meta.ai_glossary (
  term_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  term text NOT NULL CHECK (char_length(btrim(term)) BETWEEN 1 AND 200),
  meaning text NOT NULL CHECK (char_length(btrim(meaning)) BETWEEN 1 AND 4000),
  object_id bigint REFERENCES meta.ai_report_object(object_id) ON DELETE RESTRICT,
  is_active boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_by_user_id bigint
);

-- Example questions with checked SQL (few-shot for the model), bound to one object.
CREATE TABLE meta.ai_example (
  example_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  object_id bigint NOT NULL REFERENCES meta.ai_report_object(object_id) ON DELETE RESTRICT,
  question text NOT NULL CHECK (char_length(btrim(question)) BETWEEN 1 AND 1000),
  sql_text text NOT NULL CHECK (char_length(btrim(sql_text)) BETWEEN 1 AND 20000),
  is_active boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_by_user_id bigint
);

-- ------------------------------------------------------------------ request log (app)

CREATE TABLE app.ai_request_log (
  request_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id bigint NOT NULL,
  username text NOT NULL CHECK (char_length(username) BETWEEN 1 AND 255),
  request_kind text NOT NULL CHECK (request_kind IN ('HELP', 'REPORT')),
  question text NOT NULL CHECK (char_length(question) BETWEEN 1 AND 4000),
  image_attached boolean NOT NULL DEFAULT false,
  model text CHECK (char_length(model) <= 200),
  status text NOT NULL DEFAULT 'RUNNING' CHECK (status IN ('RUNNING', 'OK', 'NO_DATA', 'DENIED', 'REFUSED', 'ERROR')),
  result_code text CHECK (result_code ~ '^[A-Z][A-Z0-9_]{0,59}$'),
  sql_text text CHECK (char_length(sql_text) <= 20000),
  object_ids bigint[],
  row_count bigint CHECK (row_count >= 0),
  message text CHECK (char_length(message) <= 4000),
  started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  finished_at timestamptz,
  CONSTRAINT ai_request_log_finished CHECK ((status = 'RUNNING') = (finished_at IS NULL))
);
CREATE INDEX ai_request_log_user ON app.ai_request_log (user_id, request_id DESC);

-- ------------------------------------------------------------------ session and permissions

-- Effective permissions of a user: the same source as the API (app.auth_get_permissions).
CREATE FUNCTION app.ai_user_permissions(p_user_id bigint)
RETURNS SETOF text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, app AS $fn$
  SELECT g.permission_code FROM app.auth_get_permissions(p_user_id) g
$fn$;

-- Session user of an AI call, fail-closed: valid session, active user, the permission p_permission_code.
CREATE FUNCTION app.ai_session_user(p_token_hash text, p_permission_code text)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app AS $fn$
DECLARE
  v_user bigint;
  v_username text;
BEGIN
  IF p_token_hash IS NULL OR p_token_hash !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'Invalid authentication session'; END IF;
  IF p_permission_code IS NULL OR p_permission_code NOT IN ('AI_ASSISTANT_USE', 'AI_REPORT_RUN', 'AI_ADMIN_MANAGE') THEN
    RAISE EXCEPTION 'Unsupported AI permission';
  END IF;
  SELECT s.user_id, u.username INTO v_user, v_username
    FROM app.auth_session s JOIN app.user_account u ON u.user_id = s.user_id
   WHERE s.token_hash = p_token_hash AND s.revoked_at IS NULL AND s.expires_at > clock_timestamp() AND u.is_active = true;
  IF v_user IS NULL THEN RAISE EXCEPTION 'Authentication required'; END IF;
  IF NOT EXISTS (SELECT 1 FROM app.ai_user_permissions(v_user) c WHERE c = p_permission_code) THEN
    RAISE EXCEPTION 'AI access denied: %', p_permission_code;
  END IF;
  PERFORM set_config('dwh.actor_user_id', v_user::text, true);
  PERFORM set_config('dwh.actor_username', v_username, true);
  PERFORM set_config('dwh.security_source', 'AI_ASSISTANT', true);
  RETURN v_user;
END
$fn$;

-- ------------------------------------------------------------------ user calls (dwh_app)

-- Whitelist as seen by the session user: allowed objects in full, the others by title only.
CREATE FUNCTION app.ai_object_access(p_token_hash text)
RETURNS TABLE (object_id bigint, title text, is_allowed boolean, schema_name text, object_name text,
               description text, required_permission_code text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta AS $fn$
DECLARE
  v_user bigint;
BEGIN
  v_user := app.ai_session_user(p_token_hash, 'AI_ASSISTANT_USE');
  RETURN QUERY
  SELECT o.object_id, o.title, a.ok,
         CASE WHEN a.ok THEN o.schema_name END, CASE WHEN a.ok THEN o.object_name END,
         CASE WHEN a.ok THEN o.description END, o.required_permission_code
    FROM meta.ai_report_object o
    CROSS JOIN LATERAL (SELECT EXISTS (SELECT 1 FROM app.ai_user_permissions(v_user) c
                                        WHERE c = o.required_permission_code)
                               AND to_regclass(format('%I.%I', o.schema_name, o.object_name)) IS NOT NULL AS ok) a
   WHERE o.is_active
   ORDER BY o.object_id;
END
$fn$;

-- Start of a request: HELP needs AI_ASSISTANT_USE, REPORT also AI_REPORT_RUN.
CREATE FUNCTION app.ai_request_begin(p_token_hash text, p_kind text, p_question text, p_image_attached boolean,
                                     p_model text)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app AS $fn$
DECLARE
  v_user bigint;
  v_kind text := upper(btrim(coalesce(p_kind, '')));
  v_id bigint;
BEGIN
  IF v_kind NOT IN ('HELP', 'REPORT') THEN RAISE EXCEPTION 'Unsupported request kind'; END IF;
  v_user := app.ai_session_user(p_token_hash, 'AI_ASSISTANT_USE');
  IF v_kind = 'REPORT' THEN PERFORM app.ai_session_user(p_token_hash, 'AI_REPORT_RUN'); END IF;
  INSERT INTO app.ai_request_log (user_id, username, request_kind, question, image_attached, model)
  VALUES (v_user, coalesce(nullif(current_setting('dwh.actor_username', true), ''), 'user ' || v_user),
          v_kind, p_question, coalesce(p_image_attached, false), p_model)
  RETURNING request_id INTO v_id;
  RETURN v_id;
END
$fn$;

-- End of a request: only the user's own RUNNING request, once.
CREATE FUNCTION app.ai_request_finish(p_token_hash text, p_request_id bigint, p_status text, p_result_code text,
                                      p_sql_text text, p_object_ids bigint[], p_row_count bigint, p_message text)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app AS $fn$
DECLARE
  v_user bigint;
  v_status text := upper(btrim(coalesce(p_status, '')));
BEGIN
  v_user := app.ai_session_user(p_token_hash, 'AI_ASSISTANT_USE');
  IF v_status NOT IN ('OK', 'NO_DATA', 'DENIED', 'REFUSED', 'ERROR') THEN RAISE EXCEPTION 'Unsupported request status'; END IF;
  UPDATE app.ai_request_log r
     SET status = v_status, result_code = p_result_code, sql_text = p_sql_text, object_ids = p_object_ids,
         row_count = p_row_count, message = left(p_message, 4000), finished_at = clock_timestamp()
   WHERE r.request_id = p_request_id AND r.user_id = v_user AND r.status = 'RUNNING';
  IF NOT FOUND THEN RAISE EXCEPTION 'AI request not found or already finished'; END IF;
END
$fn$;

-- ------------------------------------------------------------------ administration (AI_ADMIN_MANAGE)

CREATE FUNCTION app.ai_request_log_list(p_token_hash text, p_limit integer)
RETURNS SETOF app.ai_request_log
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app AS $fn$
BEGIN
  PERFORM app.ai_session_user(p_token_hash, 'AI_ADMIN_MANAGE');
  IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'Limit must be 1..1000'; END IF;
  RETURN QUERY SELECT * FROM app.ai_request_log r ORDER BY r.request_id DESC LIMIT p_limit;
END
$fn$;

-- Add or change a whitelist object (by schema and name). Removal = is_active false.
CREATE FUNCTION app.ai_report_object_save(p_token_hash text, p_schema_name text, p_object_name text, p_title text,
                                          p_description text, p_required_permission_code text, p_is_active boolean)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta AS $fn$
DECLARE
  v_user bigint;
  v_id bigint;
BEGIN
  v_user := app.ai_session_user(p_token_hash, 'AI_ADMIN_MANAGE');
  IF p_schema_name IS DISTINCT FROM 'dm' THEN RAISE EXCEPTION 'Only schema dm is allowed'; END IF;
  IF p_object_name IS NULL OR p_object_name !~ '^[a-z_][a-z0-9_]{0,62}$'
     OR NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                     WHERE n.nspname = 'dm' AND c.relname = p_object_name AND c.relkind IN ('r', 'v', 'm', 'p')) THEN
    RAISE EXCEPTION 'Object not found in schema dm';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM app.permission p WHERE p.permission_code = p_required_permission_code AND p.is_active) THEN
    RAISE EXCEPTION 'Required permission not found';
  END IF;
  INSERT INTO meta.ai_report_object AS o (schema_name, object_name, title, description, required_permission_code,
                                          is_active, updated_by_user_id)
  VALUES (p_schema_name, p_object_name, btrim(p_title), p_description, p_required_permission_code,
          coalesce(p_is_active, true), v_user)
  ON CONFLICT (schema_name, object_name) DO UPDATE
     SET title = excluded.title, description = excluded.description,
         required_permission_code = excluded.required_permission_code, is_active = excluded.is_active,
         updated_at = clock_timestamp(), updated_by_user_id = v_user
  RETURNING o.object_id INTO v_id;
  RETURN v_id;
END
$fn$;

-- U2: mark a column of a whitelist object as a reference column (or unmark: is_active false).
CREATE FUNCTION app.ai_value_column_save(p_token_hash text, p_object_id bigint, p_column_name text,
                                         p_max_values integer, p_is_active boolean)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta AS $fn$
DECLARE
  v_user bigint;
  o meta.ai_report_object;
  v_id bigint;
BEGIN
  v_user := app.ai_session_user(p_token_hash, 'AI_ADMIN_MANAGE');
  SELECT * INTO o FROM meta.ai_report_object x WHERE x.object_id = p_object_id;
  IF o.object_id IS NULL THEN RAISE EXCEPTION 'Whitelist object not found'; END IF;
  IF p_column_name IS NULL OR NOT EXISTS (
       SELECT 1 FROM pg_attribute a
        WHERE a.attrelid = to_regclass(format('%I.%I', o.schema_name, o.object_name))
          AND a.attname = p_column_name AND a.attnum > 0 AND NOT a.attisdropped) THEN
    RAISE EXCEPTION 'Column not found in the object';
  END IF;
  INSERT INTO meta.ai_value_column AS v (object_id, column_name, max_values, is_active, updated_by_user_id)
  VALUES (p_object_id, p_column_name, coalesce(p_max_values, 50), coalesce(p_is_active, true), v_user)
  ON CONFLICT (object_id, column_name) DO UPDATE
     SET max_values = excluded.max_values, is_active = excluded.is_active,
         updated_at = clock_timestamp(), updated_by_user_id = v_user
  RETURNING v.value_column_id INTO v_id;
  RETURN v_id;
END
$fn$;

-- Glossary term: new when p_term_id is NULL, otherwise a change of the term.
CREATE FUNCTION app.ai_glossary_save(p_token_hash text, p_term_id bigint, p_term text, p_meaning text,
                                     p_object_id bigint, p_is_active boolean)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta AS $fn$
DECLARE
  v_user bigint;
  v_id bigint;
BEGIN
  v_user := app.ai_session_user(p_token_hash, 'AI_ADMIN_MANAGE');
  IF p_term_id IS NULL THEN
    INSERT INTO meta.ai_glossary (term, meaning, object_id, is_active, updated_by_user_id)
    VALUES (btrim(p_term), btrim(p_meaning), p_object_id, coalesce(p_is_active, true), v_user)
    RETURNING term_id INTO v_id;
  ELSE
    UPDATE meta.ai_glossary g
       SET term = btrim(p_term), meaning = btrim(p_meaning), object_id = p_object_id,
           is_active = coalesce(p_is_active, true), updated_at = clock_timestamp(), updated_by_user_id = v_user
     WHERE g.term_id = p_term_id
    RETURNING g.term_id INTO v_id;
    IF v_id IS NULL THEN RAISE EXCEPTION 'Glossary term not found'; END IF;
  END IF;
  RETURN v_id;
END
$fn$;

-- Example question with SQL: new when p_example_id is NULL, otherwise a change.
CREATE FUNCTION app.ai_example_save(p_token_hash text, p_example_id bigint, p_object_id bigint, p_question text,
                                    p_sql_text text, p_is_active boolean)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta AS $fn$
DECLARE
  v_user bigint;
  v_id bigint;
BEGIN
  v_user := app.ai_session_user(p_token_hash, 'AI_ADMIN_MANAGE');
  IF p_example_id IS NULL THEN
    INSERT INTO meta.ai_example (object_id, question, sql_text, is_active, updated_by_user_id)
    VALUES (p_object_id, btrim(p_question), btrim(p_sql_text), coalesce(p_is_active, true), v_user)
    RETURNING example_id INTO v_id;
  ELSE
    UPDATE meta.ai_example e
       SET object_id = p_object_id, question = btrim(p_question), sql_text = btrim(p_sql_text),
           is_active = coalesce(p_is_active, true), updated_at = clock_timestamp(), updated_by_user_id = v_user
     WHERE e.example_id = p_example_id
    RETURNING e.example_id INTO v_id;
    IF v_id IS NULL THEN RAISE EXCEPTION 'Example not found'; END IF;
  END IF;
  RETURN v_id;
END
$fn$;

-- ------------------------------------------------------------------ initial configuration

INSERT INTO meta.ai_report_object (schema_name, object_name, title, description, required_permission_code)
VALUES ('dm', 'v_retail_application_current', 'Заявки розничного кредитования (текущее состояние)',
        'Одна строка на заявку CRM в её последней версии: номер и дата заявки, стадия и статус, филиал, канал продаж, '
        'запрошенные, выбранные и итоговые условия кредита (сумма, продукт, валюта, срок, ставка), доход и риск-грейд заявителя.',
        'REPORT_VIEW');
INSERT INTO meta.ai_value_column (object_id, column_name)
SELECT o.object_id, c.col
  FROM meta.ai_report_object o,
       unnest(ARRAY['stage_name_ru', 'status_name_ru', 'branch_name_ru', 'sales_channel_code', 'sales_channel_name_ru',
                    'requested_product_name_ru', 'final_product_name_ru', 'requested_currency_code']) c(col)
 WHERE o.object_name = 'v_retail_application_current'
   AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = 'dm.v_retail_application_current'::regclass
                AND a.attname = c.col AND a.attnum > 0 AND NOT a.attisdropped);

-- ------------------------------------------------------------------ role dwh_ai_ro (U3, U4)

DO $role$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dwh_ai_ro') THEN
    CREATE ROLE dwh_ai_ro LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS
      CONNECTION LIMIT 5;
    RAISE NOTICE 'ROLE_CREATED dwh_ai_ro (no password: login impossible until it is set on the server)';
  ELSE
    ALTER ROLE dwh_ai_ro LOGIN NOINHERIT CONNECTION LIMIT 5;
    RAISE NOTICE 'ROLE_EXISTS dwh_ai_ro';
  END IF;
END
$role$;
ALTER ROLE dwh_ai_ro SET default_transaction_read_only = on;
ALTER ROLE dwh_ai_ro SET statement_timeout = '30s';
ALTER ROLE dwh_ai_ro SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE dwh_ai_ro SET search_path = dm;

GRANT USAGE ON SCHEMA dm TO dwh_ai_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA dm TO dwh_ai_ro;
-- New objects of dm (created by the owner of the schema) are readable as well.
DO $dmdef$
BEGIN
  EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA dm GRANT SELECT ON TABLES TO dwh_ai_ro',
                 (SELECT pg_get_userbyid(n.nspowner) FROM pg_namespace n WHERE n.nspname = 'dm'));
END
$dmdef$;

-- ------------------------------------------------------------------ privileges

REVOKE ALL ON meta.ai_report_object, meta.ai_value_column, meta.ai_glossary, meta.ai_example, app.ai_request_log
  FROM PUBLIC;
REVOKE ALL ON FUNCTION
  app.ai_user_permissions(bigint),
  app.ai_session_user(text, text),
  app.ai_object_access(text),
  app.ai_request_begin(text, text, text, boolean, text),
  app.ai_request_finish(text, bigint, text, text, text, bigint[], bigint, text),
  app.ai_request_log_list(text, integer),
  app.ai_report_object_save(text, text, text, text, text, text, boolean),
  app.ai_value_column_save(text, bigint, text, integer, boolean),
  app.ai_glossary_save(text, bigint, text, text, bigint, boolean),
  app.ai_example_save(text, bigint, bigint, text, text, boolean)
FROM PUBLIC;

-- Default privileges of schemas meta/app (rule since 005): remove every grant on the new objects except
-- the owner's and report it, then grant only what is intended.
DO $acl$
DECLARE
  r record;
BEGIN
  FOR r IN
    WITH t AS (SELECT unnest(ARRAY['meta.ai_report_object', 'meta.ai_value_column', 'meta.ai_glossary',
                                   'meta.ai_example', 'app.ai_request_log']::regclass[]) AS oid)
    SELECT DISTINCT CASE c.relkind WHEN 'S' THEN 'SEQUENCE ' ELSE 'TABLE ' END || c.oid::regclass::text AS obj, x.grantee
      FROM pg_class c, aclexplode(c.relacl) x
     WHERE (c.oid IN (SELECT oid FROM t)
            OR c.oid IN (SELECT d.objid FROM pg_depend d
                          WHERE d.refobjid IN (SELECT oid FROM t)
                            AND d.classid = 'pg_class'::regclass AND d.deptype IN ('a', 'i')))
       AND x.grantee <> c.relowner
    UNION
    SELECT 'FUNCTION ' || p.oid::regprocedure::text, x.grantee
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace, aclexplode(p.proacl) x
     WHERE n.nspname = 'app' AND p.proname LIKE 'ai\_%' AND x.grantee <> p.proowner
  LOOP
    EXECUTE format('REVOKE ALL ON %s FROM %s', r.obj,
                   CASE WHEN r.grantee = 0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    RAISE NOTICE 'DEFAULT_GRANT_REVOKED % FROM %', r.obj,
                 CASE WHEN r.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(r.grantee) END;
  END LOOP;
END
$acl$;

GRANT SELECT ON meta.ai_report_object, meta.ai_value_column, meta.ai_glossary, meta.ai_example TO dwh_app;
GRANT EXECUTE ON FUNCTION
  app.ai_object_access(text),
  app.ai_request_begin(text, text, text, boolean, text),
  app.ai_request_finish(text, bigint, text, text, text, bigint[], bigint, text),
  app.ai_request_log_list(text, integer),
  app.ai_report_object_save(text, text, text, text, text, text, boolean),
  app.ai_value_column_save(text, bigint, text, integer, boolean),
  app.ai_glossary_save(text, bigint, text, text, bigint, boolean),
  app.ai_example_save(text, bigint, bigint, text, text, boolean)
TO dwh_app;

SELECT 'MIGRATION_016_DONE' AS result;
