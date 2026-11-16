-- I17-09: saved reports of the AI assistant in "Reporting" -- package 017
-- (plan docs/DWH_I17_LOCAL_AI_ASSISTANT_PLAN_APPROVED_20261113.md, section 16; decisions O1-O5 of 16.11.2026).
--
-- O1  A saved report keeps the description and the checked SQL, never rows. It is executed again when opened,
--     with the rights of the user who opens it. The SQL is copied by the database from the user's own
--     successful REPORT request (app.ai_request_log), not taken from the client.
-- O2  Folders: PERSONAL (seen by the owner only) or SHARED (seen by everyone with REPORT_VIEW). A report is run
--     only when the viewer's role may read every whitelist object it uses (as app.ai_object_access).
-- O3  Folders and reports are changed by their owner; shared ones also by SUPERADMIN/ADMIN. Deletion is a
--     deactivation (the row stays); a folder is deleted only when it holds no active report. Every run of a
--     saved report is written to app.ai_request_log (kind SAVED_REPORT, saved_report_id), without rows.
-- O5  Nothing of 001-016 changes except app.ai_request_log: new kind SAVED_REPORT and column saved_report_id.
--     New functions are named report_folder_* / saved_report_* (check 016 counts app.ai_* functions).
-- Requires package 016. Run as the owner of schemas app, meta (dwh_admin):
--   psql -1 -v ON_ERROR_STOP=1 -f 017_ai_saved_reports.sql
DO $pre$
BEGIN
  SET LOCAL search_path = pg_catalog;
  IF to_regclass('meta.ai_report_object') IS NULL OR to_regclass('app.ai_request_log') IS NULL
     OR to_regprocedure('app.ai_user_permissions(bigint)') IS NULL THEN
    RAISE EXCEPTION 'STOP: package 016 is not applied';
  END IF;
  IF to_regclass('app.report_folder') IS NOT NULL OR to_regclass('app.saved_report') IS NOT NULL THEN
    RAISE EXCEPTION 'STOP: 017 is already applied';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = 'app.ai_request_log'::regclass
              AND attname = 'saved_report_id' AND NOT attisdropped) THEN
    RAISE EXCEPTION 'STOP: app.ai_request_log.saved_report_id already exists';
  END IF;
  IF (SELECT count(*) FROM pg_constraint WHERE conrelid = 'app.ai_request_log'::regclass AND contype = 'c'
         AND pg_get_constraintdef(oid) LIKE '%request_kind%') <> 1 THEN
    RAISE EXCEPTION 'STOP: expected exactly one check constraint on app.ai_request_log.request_kind';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM app.permission WHERE permission_code = 'REPORT_VIEW' AND is_active)
     OR NOT EXISTS (SELECT 1 FROM app.permission WHERE permission_code = 'AI_REPORT_RUN' AND is_active) THEN
    RAISE EXCEPTION 'STOP: permissions REPORT_VIEW / AI_REPORT_RUN not found';
  END IF;
END
$pre$;

-- ------------------------------------------------------------------ tables (app)

CREATE TABLE app.report_folder (
  folder_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  folder_name text NOT NULL CHECK (char_length(btrim(folder_name)) BETWEEN 1 AND 200),
  visibility text NOT NULL CHECK (visibility IN ('PERSONAL', 'SHARED')),
  owner_user_id bigint NOT NULL,
  owner_username text NOT NULL CHECK (char_length(owner_username) BETWEEN 1 AND 255),
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_by_user_id bigint NOT NULL
);
CREATE UNIQUE INDEX report_folder_shared_name ON app.report_folder (lower(btrim(folder_name)))
  WHERE is_active AND visibility = 'SHARED';
CREATE UNIQUE INDEX report_folder_personal_name ON app.report_folder (owner_user_id, lower(btrim(folder_name)))
  WHERE is_active AND visibility = 'PERSONAL';

CREATE TABLE app.saved_report (
  report_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  folder_id bigint NOT NULL REFERENCES app.report_folder(folder_id) ON DELETE RESTRICT,
  title text NOT NULL CHECK (char_length(btrim(title)) BETWEEN 1 AND 200),
  question text NOT NULL CHECK (char_length(question) BETWEEN 1 AND 4000),
  sql_text text NOT NULL CHECK (char_length(btrim(sql_text)) BETWEEN 1 AND 20000),
  object_ids bigint[] NOT NULL CHECK (cardinality(object_ids) >= 1),
  source_request_id bigint NOT NULL REFERENCES app.ai_request_log(request_id) ON DELETE RESTRICT,
  owner_user_id bigint NOT NULL,
  owner_username text NOT NULL CHECK (char_length(owner_username) BETWEEN 1 AND 255),
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_by_user_id bigint NOT NULL
);
CREATE INDEX saved_report_folder ON app.saved_report (folder_id) WHERE is_active;

-- Runs of saved reports go to the request log of 016.
ALTER TABLE app.ai_request_log ADD COLUMN saved_report_id bigint REFERENCES app.saved_report(report_id) ON DELETE RESTRICT;
DO $kind$
DECLARE
  v_name text;
BEGIN
  SELECT conname INTO v_name FROM pg_constraint
   WHERE conrelid = 'app.ai_request_log'::regclass AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%request_kind%';
  EXECUTE format('ALTER TABLE app.ai_request_log DROP CONSTRAINT %I', v_name);
  RAISE NOTICE 'REQUEST_KIND_CONSTRAINT_REPLACED %', v_name;
END
$kind$;
ALTER TABLE app.ai_request_log ADD CONSTRAINT ai_request_log_kind
  CHECK (request_kind IN ('HELP', 'REPORT', 'SAVED_REPORT'));
ALTER TABLE app.ai_request_log ADD CONSTRAINT ai_request_log_saved_report
  CHECK ((request_kind = 'SAVED_REPORT') = (saved_report_id IS NOT NULL));

-- ------------------------------------------------------------------ session and access

-- Session user for saved reports, fail-closed: valid session, active user, the permission p_permission_code.
CREATE FUNCTION app.saved_report_session_user(p_token_hash text, p_permission_code text)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app AS $fn$
DECLARE
  v_user bigint;
  v_username text;
BEGIN
  IF p_token_hash IS NULL OR p_token_hash !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'Invalid authentication session'; END IF;
  IF p_permission_code IS NULL OR p_permission_code NOT IN ('REPORT_VIEW', 'AI_REPORT_RUN') THEN
    RAISE EXCEPTION 'Unsupported permission';
  END IF;
  SELECT s.user_id, u.username INTO v_user, v_username
    FROM app.auth_session s JOIN app.user_account u ON u.user_id = s.user_id
   WHERE s.token_hash = p_token_hash AND s.revoked_at IS NULL AND s.expires_at > clock_timestamp() AND u.is_active = true;
  IF v_user IS NULL THEN RAISE EXCEPTION 'Authentication required'; END IF;
  IF NOT EXISTS (SELECT 1 FROM app.ai_user_permissions(v_user) c WHERE c = p_permission_code) THEN
    RAISE EXCEPTION 'Access denied: %', p_permission_code;
  END IF;
  PERFORM set_config('dwh.actor_user_id', v_user::text, true);
  PERFORM set_config('dwh.actor_username', v_username, true);
  PERFORM set_config('dwh.security_source', 'SAVED_REPORT', true);
  RETURN v_user;
END
$fn$;

-- Administrator in the sense of O3: active SUPERADMIN or ADMIN account.
CREATE FUNCTION app.saved_report_is_admin(p_user_id bigint)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, app AS $fn$
  SELECT EXISTS (SELECT 1 FROM app.user_account u
                  WHERE u.user_id = p_user_id AND u.is_active AND u.account_level IN ('SUPERADMIN', 'ADMIN'))
$fn$;

-- May the user read every whitelist object of the list (active, existing, permission held)?
CREATE FUNCTION app.saved_report_objects_allowed(p_user_id bigint, p_object_ids bigint[])
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, app, meta AS $fn$
  SELECT cardinality(p_object_ids) >= 1 AND NOT EXISTS (
    SELECT 1 FROM unnest(p_object_ids) i(object_id)
     WHERE NOT EXISTS (
       SELECT 1 FROM meta.ai_report_object o
        WHERE o.object_id = i.object_id AND o.is_active
          AND to_regclass(format('%I.%I', o.schema_name, o.object_name)) IS NOT NULL
          AND EXISTS (SELECT 1 FROM app.ai_user_permissions(p_user_id) c WHERE c = o.required_permission_code)))
$fn$;

-- Folder visible to the user: own personal or any active shared one.
CREATE FUNCTION app.saved_report_folder_visible(p_user_id bigint, p_folder_id bigint)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, app AS $fn$
  SELECT EXISTS (SELECT 1 FROM app.report_folder f
                  WHERE f.folder_id = p_folder_id AND f.is_active
                    AND (f.visibility = 'SHARED' OR f.owner_user_id = p_user_id))
$fn$;

-- ------------------------------------------------------------------ folders (REPORT_VIEW; create: AI_REPORT_RUN)

CREATE FUNCTION app.report_folder_list(p_token_hash text)
RETURNS TABLE (folder_id bigint, folder_name text, visibility text, owner_username text, is_own boolean,
               can_edit boolean, report_count bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app AS $fn$
DECLARE
  v_user bigint;
  v_admin boolean;
BEGIN
  v_user := app.saved_report_session_user(p_token_hash, 'REPORT_VIEW');
  v_admin := app.saved_report_is_admin(v_user);
  RETURN QUERY
  SELECT f.folder_id, f.folder_name, f.visibility, f.owner_username, f.owner_user_id = v_user,
         f.owner_user_id = v_user OR (v_admin AND f.visibility = 'SHARED'),
         (SELECT count(*) FROM app.saved_report r WHERE r.folder_id = f.folder_id AND r.is_active)
    FROM app.report_folder f
   WHERE f.is_active AND (f.visibility = 'SHARED' OR f.owner_user_id = v_user)
   ORDER BY f.visibility DESC, lower(f.folder_name), f.folder_id;
END
$fn$;

-- New folder when p_folder_id is NULL (AI_REPORT_RUN), otherwise rename / change visibility.
-- Rename: owner, or an administrator for a shared folder. Visibility: owner only.
CREATE FUNCTION app.report_folder_save(p_token_hash text, p_folder_id bigint, p_folder_name text, p_visibility text)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app AS $fn$
DECLARE
  v_user bigint;
  v_visibility text := upper(btrim(coalesce(p_visibility, '')));
  f app.report_folder;
  v_id bigint;
BEGIN
  IF v_visibility NOT IN ('PERSONAL', 'SHARED') THEN RAISE EXCEPTION 'Visibility must be PERSONAL or SHARED'; END IF;
  v_user := app.saved_report_session_user(p_token_hash, 'REPORT_VIEW');
  IF p_folder_id IS NULL THEN
    PERFORM app.saved_report_session_user(p_token_hash, 'AI_REPORT_RUN');
    INSERT INTO app.report_folder (folder_name, visibility, owner_user_id, owner_username, updated_by_user_id)
    VALUES (btrim(p_folder_name), v_visibility, v_user,
            coalesce(nullif(current_setting('dwh.actor_username', true), ''), 'user ' || v_user), v_user)
    RETURNING report_folder.folder_id INTO v_id;
    RETURN v_id;
  END IF;
  SELECT * INTO f FROM app.report_folder x WHERE x.folder_id = p_folder_id FOR UPDATE;
  IF f.folder_id IS NULL OR NOT f.is_active OR NOT (f.visibility = 'SHARED' OR f.owner_user_id = v_user) THEN
    RAISE EXCEPTION 'Folder not found';
  END IF;
  IF NOT (f.owner_user_id = v_user OR (f.visibility = 'SHARED' AND app.saved_report_is_admin(v_user))) THEN
    RAISE EXCEPTION 'Only the owner or an administrator may change this folder';
  END IF;
  IF v_visibility <> f.visibility AND f.owner_user_id <> v_user THEN
    RAISE EXCEPTION 'Only the owner may change the visibility of a folder';
  END IF;
  UPDATE app.report_folder x
     SET folder_name = btrim(p_folder_name), visibility = v_visibility,
         updated_at = clock_timestamp(), updated_by_user_id = v_user
   WHERE x.folder_id = p_folder_id;
  RETURN p_folder_id;
END
$fn$;

-- Delete = deactivate; only an empty folder (no active report).
CREATE FUNCTION app.report_folder_delete(p_token_hash text, p_folder_id bigint)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app AS $fn$
DECLARE
  v_user bigint;
  f app.report_folder;
BEGIN
  v_user := app.saved_report_session_user(p_token_hash, 'REPORT_VIEW');
  SELECT * INTO f FROM app.report_folder x WHERE x.folder_id = p_folder_id FOR UPDATE;
  IF f.folder_id IS NULL OR NOT f.is_active OR NOT (f.visibility = 'SHARED' OR f.owner_user_id = v_user) THEN
    RAISE EXCEPTION 'Folder not found';
  END IF;
  IF NOT (f.owner_user_id = v_user OR (f.visibility = 'SHARED' AND app.saved_report_is_admin(v_user))) THEN
    RAISE EXCEPTION 'Only the owner or an administrator may delete this folder';
  END IF;
  IF EXISTS (SELECT 1 FROM app.saved_report r WHERE r.folder_id = p_folder_id AND r.is_active) THEN
    RAISE EXCEPTION 'Folder is not empty';
  END IF;
  UPDATE app.report_folder x SET is_active = false, updated_at = clock_timestamp(), updated_by_user_id = v_user
   WHERE x.folder_id = p_folder_id;
END
$fn$;

-- ------------------------------------------------------------------ saved reports

-- Save the user's own successful REPORT request into a visible folder (REPORT_VIEW and AI_REPORT_RUN).
CREATE FUNCTION app.saved_report_save(p_token_hash text, p_request_id bigint, p_folder_id bigint, p_title text)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app AS $fn$
DECLARE
  v_user bigint;
  q app.ai_request_log;
  v_id bigint;
BEGIN
  v_user := app.saved_report_session_user(p_token_hash, 'REPORT_VIEW');
  PERFORM app.saved_report_session_user(p_token_hash, 'AI_REPORT_RUN');
  SELECT * INTO q FROM app.ai_request_log r WHERE r.request_id = p_request_id;
  IF q.request_id IS NULL OR q.user_id <> v_user OR q.request_kind <> 'REPORT' OR q.status <> 'OK'
     OR q.sql_text IS NULL OR coalesce(cardinality(q.object_ids), 0) = 0 THEN
    RAISE EXCEPTION 'Only an own successful report can be saved';
  END IF;
  IF NOT app.saved_report_folder_visible(v_user, p_folder_id) THEN RAISE EXCEPTION 'Folder not found'; END IF;
  INSERT INTO app.saved_report (folder_id, title, question, sql_text, object_ids, source_request_id,
                                owner_user_id, owner_username, updated_by_user_id)
  VALUES (p_folder_id, btrim(coalesce(nullif(btrim(p_title), ''), left(q.question, 200))), q.question, q.sql_text,
          q.object_ids, q.request_id, v_user, q.username, v_user)
  RETURNING saved_report.report_id INTO v_id;
  RETURN v_id;
END
$fn$;

-- Rename or move: owner, or an administrator for a report in a shared folder; the target folder must be visible.
CREATE FUNCTION app.saved_report_update(p_token_hash text, p_report_id bigint, p_folder_id bigint, p_title text)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app AS $fn$
DECLARE
  v_user bigint;
  r app.saved_report;
BEGIN
  v_user := app.saved_report_session_user(p_token_hash, 'REPORT_VIEW');
  SELECT * INTO r FROM app.saved_report x WHERE x.report_id = p_report_id FOR UPDATE;
  IF r.report_id IS NULL OR NOT r.is_active OR NOT app.saved_report_folder_visible(v_user, r.folder_id) THEN
    RAISE EXCEPTION 'Report not found';
  END IF;
  IF NOT (r.owner_user_id = v_user
          OR (app.saved_report_is_admin(v_user)
              AND EXISTS (SELECT 1 FROM app.report_folder f WHERE f.folder_id = r.folder_id AND f.visibility = 'SHARED'))) THEN
    RAISE EXCEPTION 'Only the owner or an administrator may change this report';
  END IF;
  IF NOT app.saved_report_folder_visible(v_user, p_folder_id) THEN RAISE EXCEPTION 'Folder not found'; END IF;
  UPDATE app.saved_report x
     SET title = btrim(p_title), folder_id = p_folder_id, updated_at = clock_timestamp(), updated_by_user_id = v_user
   WHERE x.report_id = p_report_id;
END
$fn$;

-- Delete = deactivate (the row and its runs in the log stay).
CREATE FUNCTION app.saved_report_delete(p_token_hash text, p_report_id bigint)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app AS $fn$
DECLARE
  v_user bigint;
  r app.saved_report;
BEGIN
  v_user := app.saved_report_session_user(p_token_hash, 'REPORT_VIEW');
  SELECT * INTO r FROM app.saved_report x WHERE x.report_id = p_report_id FOR UPDATE;
  IF r.report_id IS NULL OR NOT r.is_active OR NOT app.saved_report_folder_visible(v_user, r.folder_id) THEN
    RAISE EXCEPTION 'Report not found';
  END IF;
  IF NOT (r.owner_user_id = v_user
          OR (app.saved_report_is_admin(v_user)
              AND EXISTS (SELECT 1 FROM app.report_folder f WHERE f.folder_id = r.folder_id AND f.visibility = 'SHARED'))) THEN
    RAISE EXCEPTION 'Only the owner or an administrator may delete this report';
  END IF;
  UPDATE app.saved_report x SET is_active = false, updated_at = clock_timestamp(), updated_by_user_id = v_user
   WHERE x.report_id = p_report_id;
END
$fn$;

-- Reports of the visible folders. The SQL is returned only when the user's role may read its objects.
CREATE FUNCTION app.saved_report_list(p_token_hash text)
RETURNS TABLE (report_id bigint, folder_id bigint, title text, question text, owner_username text, is_own boolean,
               can_edit boolean, is_allowed boolean, sql_text text, created_at timestamptz, updated_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app AS $fn$
DECLARE
  v_user bigint;
  v_admin boolean;
BEGIN
  v_user := app.saved_report_session_user(p_token_hash, 'REPORT_VIEW');
  v_admin := app.saved_report_is_admin(v_user);
  RETURN QUERY
  SELECT r.report_id, r.folder_id, r.title, r.question, r.owner_username, r.owner_user_id = v_user,
         r.owner_user_id = v_user OR (v_admin AND f.visibility = 'SHARED'), a.ok,
         CASE WHEN a.ok THEN r.sql_text END, r.created_at, r.updated_at
    FROM app.saved_report r
    JOIN app.report_folder f ON f.folder_id = r.folder_id
    CROSS JOIN LATERAL (SELECT app.saved_report_objects_allowed(v_user, r.object_ids) AS ok) a
   WHERE r.is_active AND f.is_active AND (f.visibility = 'SHARED' OR f.owner_user_id = v_user)
   ORDER BY r.folder_id, lower(r.title), r.report_id;
END
$fn$;

-- Whitelist objects the user may read (schema and name), for the SQL checks of a run (REPORT_VIEW).
CREATE FUNCTION app.saved_report_allowed_objects(p_token_hash text)
RETURNS TABLE (object_id bigint, schema_name text, object_name text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app, meta AS $fn$
DECLARE
  v_user bigint;
BEGIN
  v_user := app.saved_report_session_user(p_token_hash, 'REPORT_VIEW');
  RETURN QUERY
  SELECT o.object_id, o.schema_name, o.object_name FROM meta.ai_report_object o
   WHERE app.saved_report_objects_allowed(v_user, ARRAY[o.object_id])
   ORDER BY o.object_id;
END
$fn$;

-- Start of a run: a log row SAVED_REPORT. Allowed: RUNNING row and the SQL. Not allowed: the row is finished
-- DENIED at once and no SQL is returned.
CREATE FUNCTION app.saved_report_run_begin(p_token_hash text, p_report_id bigint)
RETURNS TABLE (request_id bigint, is_allowed boolean, sql_text text, object_ids bigint[])
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app AS $fn$
DECLARE
  v_user bigint;
  r app.saved_report;
  v_ok boolean;
  v_id bigint;
BEGIN
  v_user := app.saved_report_session_user(p_token_hash, 'REPORT_VIEW');
  SELECT * INTO r FROM app.saved_report x WHERE x.report_id = p_report_id;
  IF r.report_id IS NULL OR NOT r.is_active OR NOT app.saved_report_folder_visible(v_user, r.folder_id) THEN
    RAISE EXCEPTION 'Report not found';
  END IF;
  v_ok := app.saved_report_objects_allowed(v_user, r.object_ids);
  INSERT INTO app.ai_request_log (user_id, username, request_kind, question, saved_report_id, status, result_code,
                                  object_ids, row_count, message, finished_at)
  VALUES (v_user, coalesce(nullif(current_setting('dwh.actor_username', true), ''), 'user ' || v_user),
          'SAVED_REPORT', r.title, r.report_id,
          CASE WHEN v_ok THEN 'RUNNING' ELSE 'DENIED' END,
          CASE WHEN v_ok THEN NULL ELSE 'AI_OBJECT_NOT_ALLOWED' END,
          r.object_ids, CASE WHEN v_ok THEN NULL ELSE 0 END,
          CASE WHEN v_ok THEN NULL ELSE 'not available to the role' END,
          CASE WHEN v_ok THEN NULL ELSE clock_timestamp() END)
  RETURNING ai_request_log.request_id INTO v_id;
  RETURN QUERY SELECT v_id, v_ok, CASE WHEN v_ok THEN r.sql_text END, CASE WHEN v_ok THEN r.object_ids END;
END
$fn$;

-- End of a run: only the user's own RUNNING SAVED_REPORT row, once.
CREATE FUNCTION app.saved_report_run_finish(p_token_hash text, p_request_id bigint, p_status text, p_result_code text,
                                            p_row_count bigint, p_message text)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app AS $fn$
DECLARE
  v_user bigint;
  v_status text := upper(btrim(coalesce(p_status, '')));
BEGIN
  v_user := app.saved_report_session_user(p_token_hash, 'REPORT_VIEW');
  IF v_status NOT IN ('OK', 'NO_DATA', 'DENIED', 'REFUSED', 'ERROR') THEN RAISE EXCEPTION 'Unsupported request status'; END IF;
  UPDATE app.ai_request_log q
     SET status = v_status, result_code = p_result_code, sql_text = (SELECT s.sql_text FROM app.saved_report s
                                                                        WHERE s.report_id = q.saved_report_id),
         row_count = p_row_count, message = left(p_message, 4000), finished_at = clock_timestamp()
   WHERE q.request_id = p_request_id AND q.user_id = v_user AND q.request_kind = 'SAVED_REPORT' AND q.status = 'RUNNING';
  IF NOT FOUND THEN RAISE EXCEPTION 'Run not found or already finished'; END IF;
END
$fn$;

-- ------------------------------------------------------------------ privileges

REVOKE ALL ON app.report_folder, app.saved_report FROM PUBLIC;
REVOKE ALL ON FUNCTION
  app.saved_report_session_user(text, text),
  app.saved_report_is_admin(bigint),
  app.saved_report_objects_allowed(bigint, bigint[]),
  app.saved_report_folder_visible(bigint, bigint),
  app.report_folder_list(text),
  app.report_folder_save(text, bigint, text, text),
  app.report_folder_delete(text, bigint),
  app.saved_report_save(text, bigint, bigint, text),
  app.saved_report_update(text, bigint, bigint, text),
  app.saved_report_delete(text, bigint),
  app.saved_report_list(text),
  app.saved_report_allowed_objects(text),
  app.saved_report_run_begin(text, bigint),
  app.saved_report_run_finish(text, bigint, text, text, bigint, text)
FROM PUBLIC;

-- Default privileges of schema app (rule since 005): remove every grant on the new objects except the owner's
-- and report it, then grant only what is intended. Only the objects of 017 are touched.
DO $acl$
DECLARE
  r record;
BEGIN
  FOR r IN
    WITH t AS (SELECT unnest(ARRAY['app.report_folder', 'app.saved_report']::regclass[]) AS oid)
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
     WHERE n.nspname = 'app' AND (p.proname LIKE 'saved\_report\_%' OR p.proname LIKE 'report\_folder\_%')
       AND x.grantee <> p.proowner
  LOOP
    EXECUTE format('REVOKE ALL ON %s FROM %s', r.obj,
                   CASE WHEN r.grantee = 0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    RAISE NOTICE 'DEFAULT_GRANT_REVOKED % FROM %', r.obj,
                 CASE WHEN r.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(r.grantee) END;
  END LOOP;
END
$acl$;

GRANT EXECUTE ON FUNCTION
  app.report_folder_list(text),
  app.report_folder_save(text, bigint, text, text),
  app.report_folder_delete(text, bigint),
  app.saved_report_save(text, bigint, bigint, text),
  app.saved_report_update(text, bigint, bigint, text),
  app.saved_report_delete(text, bigint),
  app.saved_report_list(text),
  app.saved_report_allowed_objects(text),
  app.saved_report_run_begin(text, bigint),
  app.saved_report_run_finish(text, bigint, text, text, bigint, text)
TO dwh_app;

SELECT 'MIGRATION_017_DONE' AS result;
