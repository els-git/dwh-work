-- I19 step 2: schema dm, schema ods and the CRM mart, catalog only (no business data).
-- Run on the server: dwh_readonly_sql.py (dwh_app, read-only transaction, rollback).
-- Q1 schemas dm and ods: owner and privileges
SELECT 'Q1' AS q, n.nspname, pg_get_userbyid(n.nspowner) AS owner, n.nspacl::text AS acl
  FROM pg_namespace n WHERE n.nspname IN ('dm', 'ods', 'stg') ORDER BY 2;
-- Q2 default privileges for new objects in dm and ods
SELECT 'Q2' AS q, n.nspname, pg_get_userbyid(d.defaclrole) AS for_role, d.defaclobjtype AS objtype, d.defaclacl::text AS acl
  FROM pg_default_acl d JOIN pg_namespace n ON n.oid = d.defaclnamespace
 WHERE n.nspname IN ('dm', 'ods') ORDER BY 2, 3, 4;
-- Q3 relations of dm: kind, owner, estimated rows, columns, privileges
SELECT 'Q3' AS q, c.relname, c.relkind, pg_get_userbyid(c.relowner) AS owner, c.reltuples::bigint AS est_rows,
       (SELECT count(*) FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped) AS columns,
       c.relacl::text AS acl
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'dm' AND c.relkind IN ('r', 'v', 'm', 'p', 'f') ORDER BY 2;
-- Q4 indexes and constraints of dm tables
SELECT 'Q4' AS q, t.relname AS table_name, i.relname AS index_name, pg_get_indexdef(i.oid) AS definition
  FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid JOIN pg_class t ON t.oid = x.indrelid
  JOIN pg_namespace n ON n.oid = t.relnamespace WHERE n.nspname = 'dm' ORDER BY 2, 3;
-- Q5 functions of dm: owner, security definer, privileges, size of the body
SELECT 'Q5' AS q, p.oid::regprocedure::text AS function, pg_get_userbyid(p.proowner) AS owner, p.prosecdef,
       p.proacl::text AS acl, length(p.prosrc) AS body_chars, md5(p.prosrc) AS body_md5
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'dm' ORDER BY 2;
-- Q6 views of dm: definition
SELECT 'Q6' AS q, c.relname, pg_get_viewdef(c.oid, true) AS definition
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'dm' AND c.relkind IN ('v', 'm') ORDER BY 2;
-- Q7 objects outside dm that depend on dm relations (views, functions)
SELECT DISTINCT 'Q7' AS q, dn.nspname || '.' || dc.relname AS dm_relation,
       CASE d.classid WHEN 'pg_rewrite'::regclass THEN (SELECT rn.nspname || '.' || rc.relname FROM pg_rewrite r
              JOIN pg_class rc ON rc.oid = r.ev_class JOIN pg_namespace rn ON rn.oid = rc.relnamespace WHERE r.oid = d.objid)
            ELSE d.classid::regclass::text || ' ' || d.objid END AS dependent
  FROM pg_depend d JOIN pg_class dc ON dc.oid = d.refobjid JOIN pg_namespace dn ON dn.oid = dc.relnamespace
 WHERE dn.nspname = 'dm' AND d.refclassid = 'pg_class'::regclass AND d.deptype = 'n' ORDER BY 2, 3;
-- Q8 relations of ods: owner, estimated rows, columns, privileges
SELECT 'Q8' AS q, c.relname, c.relkind, pg_get_userbyid(c.relowner) AS owner, c.reltuples::bigint AS est_rows,
       (SELECT count(*) FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped) AS columns,
       c.relacl::text AS acl
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'ods' AND c.relkind IN ('r', 'v', 'm', 'p') ORDER BY 2;
-- Q9 roles of the application: attributes and memberships
SELECT 'Q9' AS q, r.rolname, r.rolcanlogin, r.rolsuper, r.rolcreaterole, r.rolinherit,
       (SELECT string_agg(g.rolname, ',') FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid WHERE m.member = r.oid) AS member_of
  FROM pg_roles r WHERE r.rolname IN ('dwh_admin', 'dwh_app', 'dwh_etl', 'dwh_report', 'dwh_ai_ro') ORDER BY 2;
-- Q10 what the application roles may do in dm and ods
SELECT 'Q10' AS q, r.rolname, n.nspname,
       has_schema_privilege(r.oid, n.oid, 'USAGE') AS usage, has_schema_privilege(r.oid, n.oid, 'CREATE') AS create_priv
  FROM pg_roles r CROSS JOIN pg_namespace n
 WHERE r.rolname IN ('dwh_app', 'dwh_etl', 'dwh_report', 'dwh_ai_ro') AND n.nspname IN ('dm', 'ods') ORDER BY 2, 3;
-- Q11 body of the CRM mart functions of dm (code of the mart, needed for I18 and the engine design)
SELECT 'Q11' AS q, p.oid::regprocedure::text AS function, pg_get_functiondef(p.oid) AS definition
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'dm' AND p.prokind = 'f' ORDER BY 2
