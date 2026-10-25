BEGIN;

-- Un emplacement peut contenir des sous-emplacements (Réserve 1 > Armoire A > Tiroir 2)
ALTER TABLE stock_locations ADD COLUMN IF NOT EXISTS parent_id uuid REFERENCES stock_locations(id) ON DELETE RESTRICT;
ALTER TABLE stock_locations DROP CONSTRAINT IF EXISTS stock_locations_name_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_locations_parent_name
  ON stock_locations(COALESCE(parent_id,'00000000-0000-0000-0000-000000000000'::uuid), lower(name));
ALTER TABLE stock_locations DROP CONSTRAINT IF EXISTS stock_locations_not_own_parent;
ALTER TABLE stock_locations ADD CONSTRAINT stock_locations_not_own_parent CHECK (parent_id IS NULL OR parent_id <> id);

-- Un emplacement peut desservir plusieurs salles (et une salle plusieurs emplacements)
CREATE TABLE IF NOT EXISTS stock_location_rooms(
  stock_location_id uuid NOT NULL REFERENCES stock_locations(id) ON DELETE CASCADE,
  room_id uuid NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
  PRIMARY KEY(stock_location_id, room_id));
CREATE INDEX IF NOT EXISTS idx_stock_location_rooms_room ON stock_location_rooms(room_id);

INSERT INTO stock_location_rooms(stock_location_id, room_id)
SELECT id, room_id FROM stock_locations WHERE room_id IS NOT NULL
ON CONFLICT DO NOTHING;

-- Anti-cycle : un emplacement ne peut pas devenir son propre descendant
CREATE OR REPLACE FUNCTION trg_stock_locations_no_cycle() RETURNS trigger AS $$
BEGIN
  IF NEW.parent_id IS NOT NULL AND EXISTS(
    WITH RECURSIVE up(id, parent_id) AS (
      SELECT id, parent_id FROM stock_locations WHERE id = NEW.parent_id
      UNION ALL
      SELECT l.id, l.parent_id FROM stock_locations l JOIN up ON l.id = up.parent_id
    ) SELECT 1 FROM up WHERE id = NEW.id) THEN
    RAISE EXCEPTION 'Cycle dans la hiérarchie des emplacements';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS stock_locations_no_cycle ON stock_locations;
CREATE TRIGGER stock_locations_no_cycle BEFORE INSERT OR UPDATE OF parent_id ON stock_locations
  FOR EACH ROW EXECUTE FUNCTION trg_stock_locations_no_cycle();

-- Le stock importé est rangé dans l'emplacement racine « Réserve 1 »
CREATE OR REPLACE FUNCTION stock_default_location() RETURNS uuid AS $$
DECLARE loc uuid;
BEGIN
  SELECT id INTO loc FROM stock_locations WHERE name='Réserve 1' AND parent_id IS NULL AND active LIMIT 1;
  IF loc IS NULL THEN SELECT id INTO loc FROM stock_locations WHERE parent_id IS NULL AND active ORDER BY name LIMIT 1; END IF;
  IF loc IS NULL THEN INSERT INTO stock_locations(name) VALUES('Réserve 1') RETURNING id INTO loc; END IF;
  RETURN loc;
END $$ LANGUAGE plpgsql;

INSERT INTO schema_migrations(version) VALUES('012_stock_location_hierarchy') ON CONFLICT DO NOTHING;
COMMIT;
