-- E0 fixture of package 030 (I13-14), on top of the E0 base e0_029: the audit journal gets what the inventory of
-- 23.11.2026 found in dwh - the row guard trg_security_event_append_only and UPDATE/DELETE/TRUNCATE for dwh_etl.
-- The guard function is a stand-in (its text in dwh was not inventoried). Sandbox only.
CREATE FUNCTION audit.prevent_security_event_change() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN RAISE EXCEPTION 'Security audit events are append-only'; END
$fn$;
CREATE TRIGGER trg_security_event_append_only BEFORE DELETE OR UPDATE ON audit.security_event
  FOR EACH ROW EXECUTE FUNCTION audit.prevent_security_event_change();
GRANT USAGE ON SCHEMA audit TO dwh_etl;
GRANT INSERT, SELECT, UPDATE, DELETE, TRUNCATE ON audit.security_event TO dwh_etl;
