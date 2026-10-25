BEGIN;
ALTER TABLE scheduling_blocks ADD COLUMN IF NOT EXISTS color varchar(7) CHECK (color IS NULL OR color ~ '^#[0-9A-Fa-f]{6}$');
INSERT INTO schema_migrations(version) VALUES('014_scheduling_block_color') ON CONFLICT DO NOTHING;
COMMIT;
