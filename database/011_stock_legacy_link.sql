BEGIN;

-- Liens entre le stock historique (stockitem / stockqrunit) et le stock de l'agenda
ALTER TABLE stock_items ADD COLUMN IF NOT EXISTS legacy_stock_item_id integer UNIQUE REFERENCES stockitem(id);
ALTER TABLE stock_units ADD COLUMN IF NOT EXISTS legacy_stock_item_id integer REFERENCES stockitem(id);
ALTER TABLE stock_units ADD COLUMN IF NOT EXISTS legacy_qr_unit_id integer UNIQUE REFERENCES stockqrunit(id);
-- une seule unite "reliquat" (sans lot ni serie) par article historique
CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_units_legacy_residual
  ON stock_units(legacy_stock_item_id) WHERE legacy_qr_unit_id IS NULL AND legacy_stock_item_id IS NOT NULL;

CREATE OR REPLACE FUNCTION stock_default_location() RETURNS uuid AS $$
DECLARE loc uuid;
BEGIN
  SELECT id INTO loc FROM stock_locations WHERE name='Réserve 1' AND active ORDER BY name LIMIT 1;
  IF loc IS NULL THEN SELECT id INTO loc FROM stock_locations WHERE active ORDER BY name LIMIT 1; END IF;
  IF loc IS NULL THEN INSERT INTO stock_locations(name) VALUES('Réserve 1') RETURNING id INTO loc; END IF;
  RETURN loc;
END $$ LANGUAGE plpgsql;

-- Article historique -> article agenda + unite reliquat
CREATE OR REPLACE FUNCTION stock_sync_item(p_item_id integer) RETURNS void AS $$
DECLARE it stockitem%ROWTYPE; sid uuid; tracked numeric; residual numeric;
BEGIN
  SELECT * INTO it FROM stockitem WHERE id=p_item_id;
  IF NOT FOUND THEN RETURN; END IF;
  INSERT INTO stock_items(sku,name,track_lot,track_serial,consumable,active,legacy_stock_item_id)
  VALUES('STK-'||it.id, it.denomination,
         EXISTS(SELECT 1 FROM stockqrunit WHERE stock_item_id=it.id AND numero_lot IS NOT NULL AND numero_lot<>''),
         EXISTS(SELECT 1 FROM stockqrunit WHERE stock_item_id=it.id AND numero_serie IS NOT NULL AND numero_serie<>''),
         true, true, it.id)
  ON CONFLICT(legacy_stock_item_id) DO UPDATE SET name=excluded.name, active=true,
    track_lot=excluded.track_lot, track_serial=excluded.track_serial
  RETURNING id INTO sid;

  SELECT COALESCE(SUM(quantity),0) INTO tracked FROM stockqrunit WHERE stock_item_id=it.id;
  residual := GREATEST(it.quantity - tracked, 0);
  IF residual > 0 OR EXISTS(SELECT 1 FROM stock_units WHERE legacy_stock_item_id=it.id AND legacy_qr_unit_id IS NULL) THEN
    INSERT INTO stock_units(stock_location_id,stock_item_id,quantity,legacy_stock_item_id)
    VALUES(stock_default_location(), sid, residual, it.id)
    ON CONFLICT(legacy_stock_item_id) WHERE legacy_qr_unit_id IS NULL AND legacy_stock_item_id IS NOT NULL
    DO UPDATE SET quantity=GREATEST(excluded.quantity, stock_units.reserved_quantity);
  END IF;
END $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION stock_sync_qr_unit(p_unit_id integer) RETURNS void AS $$
DECLARE u stockqrunit%ROWTYPE; sid uuid;
BEGIN
  SELECT * INTO u FROM stockqrunit WHERE id=p_unit_id;
  IF NOT FOUND THEN RETURN; END IF;
  PERFORM stock_sync_item(u.stock_item_id);
  SELECT id INTO sid FROM stock_items WHERE legacy_stock_item_id=u.stock_item_id;
  INSERT INTO stock_units(stock_location_id,stock_item_id,lot_number,serial_number,expires_on,quantity,legacy_stock_item_id,legacy_qr_unit_id)
  VALUES(stock_default_location(), sid, NULLIF(u.numero_lot,''), NULLIF(u.numero_serie,''), u.expiration_date, u.quantity, u.stock_item_id, u.id)
  ON CONFLICT(legacy_qr_unit_id) DO UPDATE SET lot_number=excluded.lot_number, serial_number=excluded.serial_number,
    expires_on=excluded.expires_on, quantity=GREATEST(excluded.quantity, stock_units.reserved_quantity);
  PERFORM stock_sync_item(u.stock_item_id);
END $$ LANGUAGE plpgsql;

-- Stock historique -> agenda (ignore les ecritures declenchees par la synchronisation inverse)
CREATE OR REPLACE FUNCTION trg_stockitem_sync() RETURNS trigger AS $$
BEGIN
  IF pg_trigger_depth() > 1 THEN RETURN NULL; END IF;
  PERFORM stock_sync_item(NEW.id);
  RETURN NULL;
END $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION trg_stockitem_delete() RETURNS trigger AS $$
BEGIN
  UPDATE stock_items SET active=false, legacy_stock_item_id=NULL WHERE legacy_stock_item_id=OLD.id;
  UPDATE stock_units SET legacy_stock_item_id=NULL WHERE legacy_stock_item_id=OLD.id AND legacy_qr_unit_id IS NULL;
  RETURN OLD;
END $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION trg_qrunit_sync() RETURNS trigger AS $$
BEGIN
  IF pg_trigger_depth() > 1 THEN RETURN NULL; END IF;
  PERFORM stock_sync_qr_unit(NEW.id);
  RETURN NULL;
END $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION trg_qrunit_delete() RETURNS trigger AS $$
DECLARE sid uuid;
BEGIN
  SELECT id INTO sid FROM stock_units WHERE legacy_qr_unit_id=OLD.id;
  IF sid IS NOT NULL THEN
    IF EXISTS(SELECT 1 FROM stock_movements WHERE stock_unit_id=sid)
       OR EXISTS(SELECT 1 FROM appointment_stock_usage WHERE stock_unit_id=sid) THEN
      UPDATE stock_units SET quantity=0, reserved_quantity=0, legacy_qr_unit_id=NULL WHERE id=sid;
    ELSE
      DELETE FROM stock_units WHERE id=sid;
    END IF;
  END IF;
  RETURN OLD;
END $$ LANGUAGE plpgsql;

-- Agenda -> stock historique : une sortie d'une unite reliee decremente aussi l'historique
CREATE OR REPLACE FUNCTION trg_stock_units_to_legacy() RETURNS trigger AS $$
DECLARE delta numeric;
BEGIN
  IF pg_trigger_depth() > 1 OR NEW.legacy_stock_item_id IS NULL OR NEW.quantity = OLD.quantity THEN RETURN NULL; END IF;
  delta := OLD.quantity - NEW.quantity;
  IF NEW.legacy_qr_unit_id IS NOT NULL THEN
    UPDATE stockqrunit SET quantity=GREATEST(quantity-delta::integer,0) WHERE id=NEW.legacy_qr_unit_id;
  END IF;
  UPDATE stockitem SET quantity=GREATEST(quantity-delta::integer,0) WHERE id=NEW.legacy_stock_item_id;
  RETURN NULL;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS stockitem_sync ON stockitem;
CREATE TRIGGER stockitem_sync AFTER INSERT OR UPDATE ON stockitem FOR EACH ROW EXECUTE FUNCTION trg_stockitem_sync();
DROP TRIGGER IF EXISTS stockitem_delete ON stockitem;
CREATE TRIGGER stockitem_delete BEFORE DELETE ON stockitem FOR EACH ROW EXECUTE FUNCTION trg_stockitem_delete();
DROP TRIGGER IF EXISTS qrunit_sync ON stockqrunit;
CREATE TRIGGER qrunit_sync AFTER INSERT OR UPDATE ON stockqrunit FOR EACH ROW EXECUTE FUNCTION trg_qrunit_sync();
DROP TRIGGER IF EXISTS qrunit_delete ON stockqrunit;
CREATE TRIGGER qrunit_delete BEFORE DELETE ON stockqrunit FOR EACH ROW EXECUTE FUNCTION trg_qrunit_delete();
DROP TRIGGER IF EXISTS stock_units_to_legacy ON stock_units;
CREATE TRIGGER stock_units_to_legacy AFTER UPDATE OF quantity ON stock_units FOR EACH ROW EXECUTE FUNCTION trg_stock_units_to_legacy();

-- Import initial
SELECT stock_sync_item(id) FROM stockitem;
SELECT stock_sync_qr_unit(id) FROM stockqrunit;

INSERT INTO schema_migrations(version) VALUES('011_stock_legacy_link') ON CONFLICT DO NOTHING;
COMMIT;
