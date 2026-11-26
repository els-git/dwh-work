-- Rollback of package 037: gives back exactly the EXECUTE privileges kept in audit.revoked_grant_037.
-- Run as dwh_admin:  psql -1 -v ON_ERROR_STOP=1 -f 037_ai_hidden_query_functions.down.sql
DO $pre$
BEGIN
  IF to_regclass('audit.revoked_grant_037') IS NULL THEN RAISE EXCEPTION 'STOP: 037 is not applied'; END IF;
END
$pre$;

DO $back$
DECLARE g record; n int := 0;
BEGIN
  FOR g IN SELECT * FROM audit.revoked_grant_037 ORDER BY 1, 2 LOOP
    EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %s', g.function_signature,
                   CASE WHEN g.grantee = 'PUBLIC' THEN 'PUBLIC' ELSE quote_ident(g.grantee) END);
    n := n + 1;
  END LOOP;
  RAISE NOTICE 'PRIVILEGES_RESTORED %', n;
END
$back$;

DROP TABLE audit.revoked_grant_037;

SELECT 'ROLLBACK_037_DONE' AS result;
