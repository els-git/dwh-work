-- E0 ONLY (sandbox): tables of DM marts that 021 reads for the impact of ODS changes (definitions of 018,
-- column deleted_at of 019). Order of E0: E0 database of 015 (e0_015: 008-015 applied) -> this file -> 021.
-- 016-019 need their own fixtures on an empty database and are not applied here. Never run against dwh.
-- In dwh schemas stg and ods and their tables are owned by dwh_admin (009 and 013 check it); the E0 database of 015
-- has them owned by postgres, so they are handed over here.
-- Run as a superuser; objects are owned by dwh_admin.
ALTER SCHEMA stg OWNER TO dwh_admin;
ALTER SCHEMA ods OWNER TO dwh_admin;
DO $own$
DECLARE r record;
BEGIN
  FOR r IN SELECT c.oid::regclass AS rel FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname IN ('stg', 'ods') AND c.relkind IN ('r', 'p') LOOP
    EXECUTE format('ALTER TABLE %s OWNER TO dwh_admin', r.rel);
  END LOOP;
END
$own$;
-- dwh: tables created by dwh_admin in stg and ods are written by dwh_etl (checks 009 and 013, item 5).
ALTER DEFAULT PRIVILEGES FOR ROLE dwh_admin IN SCHEMA stg GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO dwh_etl;
ALTER DEFAULT PRIVILEGES FOR ROLE dwh_admin IN SCHEMA ods GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO dwh_etl;
SET ROLE dwh_admin;
CREATE TABLE meta.dm_entity (
  entity_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  entity_code text NOT NULL UNIQUE CHECK (entity_code ~ '^[A-Z][A-Z0-9_]{0,59}$'),
  title text NOT NULL CHECK (char_length(btrim(title)) BETWEEN 1 AND 200),
  is_active boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_by_user_id bigint
);

CREATE TABLE meta.dm_entity_key (
  entity_key_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  entity_id bigint NOT NULL REFERENCES meta.dm_entity(entity_id) ON DELETE RESTRICT,
  table_schema text NOT NULL DEFAULT 'ods' CHECK (table_schema = 'ods'),
  table_name text NOT NULL CHECK (table_name ~ '^[a-z_][a-z0-9_]{0,62}$'),
  column_name text NOT NULL CHECK (column_name ~ '^[a-z_][a-z0-9_]{0,62}$'),
  type_family text NOT NULL CHECK (char_length(type_family) BETWEEN 1 AND 63),
  is_active boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_by_user_id bigint,
  CONSTRAINT dm_entity_key_table UNIQUE (entity_id, table_schema, table_name)
);

-- ------------------------------------------------------------------ marts and versions

CREATE TABLE meta.dm_mart (
  mart_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  mart_code text NOT NULL UNIQUE CHECK (mart_code ~ '^[a-z][a-z0-9_]{0,49}$'),
  title text NOT NULL CHECK (char_length(btrim(title)) BETWEEN 1 AND 200),
  description text CHECK (char_length(description) <= 4000),
  required_permission_code text NOT NULL DEFAULT 'REPORT_VIEW' CHECK (required_permission_code ~ '^[A-Z][A-Z0-9_]*$'),
  ai_enabled boolean NOT NULL DEFAULT false,
  auto_after_ods boolean NOT NULL DEFAULT true,
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  created_by_user_id bigint NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_by_user_id bigint NOT NULL
);

CREATE TABLE meta.dm_mart_version (
  version_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  mart_id bigint NOT NULL REFERENCES meta.dm_mart(mart_id) ON DELETE RESTRICT,
  version_no integer NOT NULL CHECK (version_no >= 1),
  status text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'PUBLISHED', 'SUPERSEDED')),
  grain_entity_id bigint REFERENCES meta.dm_entity(entity_id) ON DELETE RESTRICT,
  ddl_status text NOT NULL DEFAULT 'NONE' CHECK (ddl_status IN ('NONE', 'PROPOSED', 'APPLIED', 'REJECTED')),
  ddl_text text CHECK (char_length(ddl_text) <= 100000),
  ddl_sha256 text CHECK (ddl_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  created_by_user_id bigint NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  published_at timestamptz,
  published_by_user_id bigint,
  CONSTRAINT dm_mart_version_no UNIQUE (mart_id, version_no),
  CONSTRAINT dm_mart_version_ddl CHECK ((ddl_status = 'NONE') = (ddl_text IS NULL) AND (ddl_text IS NULL) = (ddl_sha256 IS NULL)),
  CONSTRAINT dm_mart_version_published CHECK ((status = 'DRAFT') = (published_at IS NULL))
);
CREATE UNIQUE INDEX dm_mart_version_one_published ON meta.dm_mart_version (mart_id) WHERE status = 'PUBLISHED';
CREATE UNIQUE INDEX dm_mart_version_one_draft ON meta.dm_mart_version (mart_id) WHERE status = 'DRAFT';

-- Sources of a version. GRAIN / SAME_KEY / CHILD join by the key of the grain entity (key_column of the
-- table in meta.dm_entity_key, copied when the version is saved); LOOKUP joins lookup_column of its own
-- table to ref_column of an earlier non-CHILD source (D12; roles = the same table under several aliases).
CREATE TABLE meta.dm_mart_source (
  source_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  version_id bigint NOT NULL REFERENCES meta.dm_mart_version(version_id) ON DELETE CASCADE,
  position integer NOT NULL CHECK (position >= 1),
  alias text NOT NULL CHECK (alias ~ '^[a-z][a-z0-9_]{0,30}$'),
  table_schema text NOT NULL DEFAULT 'ods' CHECK (table_schema = 'ods'),
  table_name text NOT NULL CHECK (table_name ~ '^[a-z_][a-z0-9_]{0,62}$'),
  source_kind text NOT NULL CHECK (source_kind IN ('GRAIN', 'SAME_KEY', 'CHILD', 'LOOKUP')),
  key_column text CHECK (key_column ~ '^[a-z_][a-z0-9_]{0,62}$'),
  ref_alias text CHECK (ref_alias ~ '^[a-z][a-z0-9_]{0,30}$'),
  ref_column text CHECK (ref_column ~ '^[a-z_][a-z0-9_]{0,62}$'),
  CONSTRAINT dm_mart_source_alias UNIQUE (version_id, alias),
  CONSTRAINT dm_mart_source_position UNIQUE (version_id, position),
  CONSTRAINT dm_mart_source_shape CHECK (key_column IS NOT NULL
    AND (source_kind = 'LOOKUP') = (ref_alias IS NOT NULL AND ref_column IS NOT NULL)
    AND (source_kind = 'LOOKUP' OR (ref_alias IS NULL AND ref_column IS NULL)))
);
CREATE UNIQUE INDEX dm_mart_source_one_grain ON meta.dm_mart_source (version_id) WHERE source_kind = 'GRAIN';

CREATE TABLE meta.dm_mart_field (
  field_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  version_id bigint NOT NULL REFERENCES meta.dm_mart_version(version_id) ON DELETE CASCADE,
  position integer NOT NULL CHECK (position >= 1),
  field_name text NOT NULL CHECK (field_name ~ '^[a-z][a-z0-9_]{0,62}$' AND field_name <> 'dm_loaded_at'),
  title text CHECK (char_length(title) <= 200),
  data_type text NOT NULL CHECK (data_type ~ '^(text|bigint|integer|smallint|numeric|numeric\([1-9][0-9]?,[0-9][0-9]?\)|date|timestamp|timestamptz|boolean|varchar\([1-9][0-9]{0,3}\))$'),
  is_key boolean NOT NULL DEFAULT false,
  CONSTRAINT dm_mart_field_name UNIQUE (version_id, field_name),
  CONSTRAINT dm_mart_field_position UNIQUE (version_id, position)
);
CREATE UNIQUE INDEX dm_mart_field_one_key ON meta.dm_mart_field (version_id) WHERE is_key;

CREATE TABLE meta.dm_mart_field_source (
  field_source_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  field_id bigint NOT NULL REFERENCES meta.dm_mart_field(field_id) ON DELETE CASCADE,
  priority_no integer NOT NULL CHECK (priority_no BETWEEN 1 AND 9),
  source_alias text NOT NULL CHECK (source_alias ~ '^[a-z][a-z0-9_]{0,30}$'),
  source_column text NOT NULL CHECK (source_column ~ '^[a-z_][a-z0-9_]{0,62}$'),
  aggregate_fn text NOT NULL DEFAULT 'NONE' CHECK (aggregate_fn IN ('NONE', 'SUM', 'COUNT', 'COUNT_DISTINCT', 'MIN', 'MAX', 'AVG')),
  CONSTRAINT dm_mart_field_source_priority UNIQUE (field_id, priority_no)
);

-- value: JSON scalar for comparisons and LIKE, JSON array (1-100 scalars) for IN, null for IS [NOT] NULL.
CREATE TABLE meta.dm_mart_filter (
  filter_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  version_id bigint NOT NULL REFERENCES meta.dm_mart_version(version_id) ON DELETE CASCADE,
  position integer NOT NULL CHECK (position >= 1),
  source_alias text NOT NULL CHECK (source_alias ~ '^[a-z][a-z0-9_]{0,30}$'),
  column_name text NOT NULL CHECK (column_name ~ '^[a-z_][a-z0-9_]{0,62}$'),
  operator text NOT NULL CHECK (operator IN ('=', '<>', '>', '>=', '<', '<=', 'IN', 'IS NULL', 'IS NOT NULL', 'LIKE')),
  value jsonb,
  CONSTRAINT dm_mart_filter_position UNIQUE (version_id, position),
  CONSTRAINT dm_mart_filter_value CHECK (CASE
    WHEN operator IN ('IS NULL', 'IS NOT NULL') THEN value IS NULL
    WHEN operator = 'IN' THEN jsonb_typeof(value) = 'array' AND jsonb_array_length(value) BETWEEN 1 AND 100
    ELSE jsonb_typeof(value) IN ('string', 'number', 'boolean') END)
);
ALTER TABLE meta.dm_mart ADD COLUMN deleted_at timestamptz, ADD COLUMN deleted_by_user_id bigint;
RESET ROLE;
