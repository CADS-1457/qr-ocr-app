-- Patient temporaire : créé depuis l'agenda tant que le dossier n'est pas connu, à réconcilier à l'accueil.
ALTER TABLE patient ADD COLUMN IF NOT EXISTS is_temporary BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS ix_patient_is_temporary ON patient (is_temporary) WHERE is_temporary;
