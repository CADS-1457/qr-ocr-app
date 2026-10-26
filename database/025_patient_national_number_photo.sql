-- Numéro national (identité eID) et photo de la carte
ALTER TABLE patient ADD COLUMN IF NOT EXISTS numero_national VARCHAR(11);
ALTER TABLE patient ADD COLUMN IF NOT EXISTS photo BYTEA;
CREATE INDEX IF NOT EXISTS ix_patient_numero_national ON patient (numero_national);
