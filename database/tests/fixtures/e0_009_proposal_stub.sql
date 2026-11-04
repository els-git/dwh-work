-- E0 ONLY (sandbox): the change proposal of I05 step 6 as stored in dwh on 04.11.2026
-- (object public.asan_pension_requests, a draft and proposal with the reviewed DDL), and
-- the default privileges of schema stg read in I05 step 2. Never run against dwh or a copy.
ALTER DEFAULT PRIVILEGES IN SCHEMA stg GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO dwh_etl;
GRANT USAGE ON SCHEMA stg TO dwh_etl;
INSERT INTO meta.source_system(code, name, source_type) VALUES ('OPERATION_COLLECTOR', 'oc', 'POSTGRESQL');
INSERT INTO meta.source_object(source_system_id, source_schema, source_object)
SELECT source_system_id, 'public', 'asan_pension_requests' FROM meta.source_system WHERE code = 'OPERATION_COLLECTOR';
INSERT INTO meta.object_config_version(source_object_id, version_no, target_schema, target_table, created_by, updated_by)
SELECT source_object_id, 1, 'stg', 'operation_collector_asan_pension_requests', 1, 1
  FROM meta.source_object WHERE source_object = 'asan_pension_requests';
INSERT INTO meta.target_change_proposal(source_object_id, version_id, change_kind, target_schema, target_table, ddl, created_by)
SELECT v.source_object_id, v.version_id, 'CREATE_TABLE', 'stg', 'operation_collector_asan_pension_requests', $ddl$-- I05 change proposal for object 16 (public.asan_pension_requests); apply only as a database package (E0 -> E1 -> E2).
CREATE TABLE "stg"."operation_collector_asan_pension_requests" (
  "id" bigint NOT NULL,
  "pin" varchar(16),
  "name" varchar(256),
  "surname" varchar(256),
  "meta_data" jsonb,
  "patronymic" varchar(256),
  "birth_date" varchar(16),
  "is_success" boolean,
  "is_cache" boolean,
  "has_meta_data" boolean,
  "is_paid" boolean,
  "asan_response_status" varchar(32),
  "message" text,
  "interval_days" bigint,
  "channel" varchar(64),
  "product_type" varchar(64),
  "created_at" timestamp,
  CONSTRAINT "pk_operation_collector_asan_pension_requests" PRIMARY KEY ("id")
);
$ddl$, 1
  FROM meta.object_config_version v WHERE v.target_table = 'operation_collector_asan_pension_requests';
