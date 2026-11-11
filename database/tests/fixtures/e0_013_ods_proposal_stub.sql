-- E0 ONLY (sandbox): the ODS change proposals of I06-05 step 7 as made in the UI on 11.11.2026
-- (object public.asan_pension_requests: an ODS draft, proposal #2 with birth_date as text and
-- proposal #3 with birth_date as date, the reviewed DDL). Requires 012 and the E0 fixtures of 012
-- (default privileges of schema ods). Never run against dwh or a copy.
INSERT INTO meta.object_ods_version(source_object_id, version_no, target_table, created_by, updated_by)
SELECT source_object_id, 1, 'operation_collector_asan_pension_requests', 1, 1 FROM meta.source_object WHERE source_object = 'asan_pension_requests';
INSERT INTO meta.target_change_proposal(source_object_id, ods_version_id, change_kind, target_schema, target_table, ddl, created_by)
SELECT v.source_object_id, v.ods_version_id, 'CREATE_TABLE', 'ods', 'operation_collector_asan_pension_requests', d.ddl, 1
  FROM meta.object_ods_version v,
       (VALUES (1, $ddl2$-- I06-05 change proposal for ODS of object 16 (from stg.operation_collector_asan_pension_requests); apply only as a database package (E0 -> E1 -> E2).
CREATE TABLE "ods"."operation_collector_asan_pension_requests" (
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
  "loaded_at" timestamptz,
  CONSTRAINT "pk_operation_collector_asan_pension_requests" PRIMARY KEY ("id")
);
$ddl2$), (2, $ddl3$-- I06-05 change proposal for ODS of object 16 (from stg.operation_collector_asan_pension_requests); apply only as a database package (E0 -> E1 -> E2).
CREATE TABLE "ods"."operation_collector_asan_pension_requests" (
  "id" bigint NOT NULL,
  "pin" varchar(16),
  "name" varchar(256),
  "surname" varchar(256),
  "meta_data" jsonb,
  "patronymic" varchar(256),
  "birth_date" date,
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
  "loaded_at" timestamptz,
  CONSTRAINT "pk_operation_collector_asan_pension_requests" PRIMARY KEY ("id")
);
$ddl3$)) AS d(n, ddl)
 WHERE v.target_table = 'operation_collector_asan_pension_requests'
 ORDER BY d.n;
