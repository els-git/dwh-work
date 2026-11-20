-- E0 ONLY (sandbox): bring the stub meta.source_object to the columns and constraints read from dwh on
-- 20.11.2026 (database/diagnostics/i03_05_object_identity_inventory.sql), so that 028 is tested on the same
-- shape (column sizes stay as in the stub: a view of 015 depends on them). Defaults of dwh were not read; the stub gives defaults only so that older E0 inserts keep working.
-- Never run against dwh or a copy of it.
ALTER TABLE meta.source_object
  ADD COLUMN IF NOT EXISTS load_strategy varchar(30) NOT NULL DEFAULT 'FULL',
  ADD COLUMN IF NOT EXISTS watermark_column varchar(128),
  ADD COLUMN IF NOT EXISTS target_schema varchar(128),
  ADD COLUMN IF NOT EXISTS target_object varchar(128),
  ADD COLUMN IF NOT EXISTS load_order integer NOT NULL DEFAULT 100,
  ADD COLUMN IF NOT EXISTS description text,
  ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE meta.source_object
  ADD CONSTRAINT source_object_object_type_check CHECK (object_type IN ('TABLE', 'VIEW')),
  ADD CONSTRAINT source_object_load_strategy_check CHECK (load_strategy IN ('FULL', 'INCREMENTAL', 'INCREMENTAL_OVERLAP', 'SNAPSHOT')),
  ADD CONSTRAINT uq_source_object UNIQUE (source_system_id, source_schema, source_object);
