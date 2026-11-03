-- I03 step 2: how existing app.integration_* functions check the session and
-- permission, so that app.integration_profile_* can follow the same mechanism.
-- Read-only: function definitions and signatures from pg_catalog (no table data,
-- no secrets). Statements must not contain ';' inside string literals.
SELECT current_user || ' | ' || current_database() || ' | ' || current_setting('server_version');

-- Signatures of app functions related to integrations, sessions and permissions.
SELECT p.oid::regprocedure::text || ' | secdef=' || p.prosecdef::text
       || ' | owner=' || pg_get_userbyid(p.proowner)
       || ' | dwh_app_execute=' || has_function_privilege('dwh_app', p.oid, 'EXECUTE')::text
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname = 'app'
   AND (p.proname LIKE 'integration\_source\_system%' OR p.proname LIKE 'auth%'
        OR p.proname LIKE '%permission%' OR p.proname LIKE '%session%' OR p.proname LIKE '%audit%')
 ORDER BY 1;

-- Full definition of the reference function (create of a source system).
SELECT pg_get_functiondef(p.oid)
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname = 'app' AND p.proname = 'integration_source_system_create';
