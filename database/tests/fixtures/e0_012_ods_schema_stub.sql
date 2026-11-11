-- E0 ONLY (sandbox): privileges of schema ods as in dwh (I05 step 2, 04.11.2026): dwh_etl has
-- USAGE and default read/write privileges on new tables; dwh_app has no USAGE. Never run against dwh.
GRANT USAGE ON SCHEMA ods TO dwh_etl;
ALTER DEFAULT PRIVILEGES IN SCHEMA ods GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO dwh_etl;
