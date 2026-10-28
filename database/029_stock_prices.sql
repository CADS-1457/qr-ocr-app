-- Prix des produits du stock avec période de validité.
-- Un prix sans date de fin reste valable pour les délivrances futures ; un nouveau prix à une date d'application
-- clôture automatiquement le prix ouvert précédent (valid_to = veille de la nouvelle date).
CREATE TABLE IF NOT EXISTS stock_prices(
  id SERIAL PRIMARY KEY,
  stock_item_id INTEGER NOT NULL REFERENCES stockitem(id) ON DELETE CASCADE,
  price NUMERIC(12,2) NOT NULL CHECK(price >= 0),
  valid_from DATE NOT NULL,
  valid_to DATE,
  created_by VARCHAR(255),
  created_at TIMESTAMP NOT NULL DEFAULT now(),
  CHECK(valid_to IS NULL OR valid_to >= valid_from)
);
CREATE INDEX IF NOT EXISTS ix_stock_prices_item ON stock_prices(stock_item_id, valid_from);
