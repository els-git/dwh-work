-- I13-14 step 1: who can change or delete records of the journals (read only, no data of the journals).
-- Run on the server as postgres in a read-only transaction:
--   sudo runuser -u postgres -- psql -X -d dwh -v ON_ERROR_STOP=1 < /srv/dwh/incoming/i13_14_audit_write_inventory.sql
SET default_transaction_read_only = on;
BEGIN;
SHOW transaction_read_only;

\echo '== 1. superusers and roles that bypass row security (they bypass every right)'
SELECT rolname, rolsuper, rolbypassrls, rolcanlogin FROM pg_roles
 WHERE rolsuper OR rolbypassrls ORDER BY 1;

\echo '== 2. rights on the journal tables per role (owner and grants, including through role membership)'
WITH t AS (SELECT c.oid, n.nspname || '.' || c.relname AS journal, c.relowner::regrole AS owner
             FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE (n.nspname, c.relname) IN (('audit', 'security_event'), ('secret_store', 'operation_event'),
                                             ('secret_store', 'binding')))
SELECT t.journal, t.owner, r.rolname,
       has_table_privilege(r.oid, t.oid, 'INSERT') AS can_insert,
       has_table_privilege(r.oid, t.oid, 'UPDATE') AS can_update,
       has_table_privilege(r.oid, t.oid, 'DELETE') AS can_delete,
       has_table_privilege(r.oid, t.oid, 'TRUNCATE') AS can_truncate
  FROM t CROSS JOIN pg_roles r
 WHERE r.rolname !~ '^pg_' AND NOT r.rolsuper
   AND (has_table_privilege(r.oid, t.oid, 'UPDATE') OR has_table_privilege(r.oid, t.oid, 'DELETE')
        OR has_table_privilege(r.oid, t.oid, 'TRUNCATE') OR has_table_privilege(r.oid, t.oid, 'INSERT'))
 ORDER BY 1, 3;

\echo '== 3. members of the roles that can change the journals (who inherits these rights)'
SELECT g.rolname AS granted_role, m.rolname AS member, m.rolcanlogin AS member_can_login, a.inherit_option
  FROM pg_auth_members a JOIN pg_roles g ON g.oid = a.roleid JOIN pg_roles m ON m.oid = a.member
 WHERE g.rolname IN ('dwh_admin', 'dwh_etl', 'dwh_app') OR m.rolname IN ('dwh_admin', 'dwh_etl', 'dwh_app')
 ORDER BY 1, 2;

\echo '== 4. triggers that guard the journals (UPDATE / DELETE / TRUNCATE)'
SELECT c.relnamespace::regnamespace || '.' || c.relname AS journal, tg.tgname, tg.tgenabled,
       pg_get_triggerdef(tg.oid) AS definition
  FROM pg_trigger tg JOIN pg_class c ON c.oid = tg.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE NOT tg.tgisinternal
   AND (n.nspname, c.relname) IN (('audit', 'security_event'), ('secret_store', 'operation_event'), ('secret_store', 'binding'))
 ORDER BY 1, 2;

\echo '== 5. functions that change or delete journal rows (any schema), with who may call them'
SELECT n.nspname || '.' || p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')' AS function,
       p.prosecdef AS security_definer, p.proowner::regrole AS owner, p.proacl
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
   AND p.prosrc ~* '(update|delete\s+from|truncate)\s+(only\s+)?(audit\.security_event|secret_store\.operation_event)'
 ORDER BY 1;

ROLLBACK;
\echo '== I13_14_INVENTORY_DONE'
