-- E0 fixture of package 031 (I13-15), on top of the E0 base e0_030: the catalogs of marts and entities as created
-- by packages 018 and 019 in dwh (columns, types, required columns); the E0 chain 027-030 has no marts. Sandbox only.
CREATE TABLE meta.dm_entity (
  entity_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  entity_code text NOT NULL UNIQUE CHECK (entity_code ~ '^[A-Z][A-Z0-9_]{0,59}$'),
  title text NOT NULL CHECK (char_length(btrim(title)) BETWEEN 1 AND 200),
  is_active boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_by_user_id bigint
);
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
  updated_by_user_id bigint NOT NULL,
  deleted_at timestamptz,
  deleted_by_user_id bigint
);
ALTER TABLE meta.dm_entity OWNER TO dwh_admin;
ALTER TABLE meta.dm_mart OWNER TO dwh_admin;
