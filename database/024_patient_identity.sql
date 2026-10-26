-- Signalétique patient : informations issues de la carte d'identité belge (le numéro national n'est pas conservé).
ALTER TABLE patient ADD COLUMN IF NOT EXISTS prenoms VARCHAR(255);
ALTER TABLE patient ADD COLUMN IF NOT EXISTS lieu_naissance VARCHAR(255);
ALTER TABLE patient ADD COLUMN IF NOT EXISTS sexe VARCHAR(1);
ALTER TABLE patient ADD COLUMN IF NOT EXISTS nationalite VARCHAR(100);
ALTER TABLE patient ADD COLUMN IF NOT EXISTS adresse VARCHAR(255);
ALTER TABLE patient ADD COLUMN IF NOT EXISTS code_postal VARCHAR(20);
ALTER TABLE patient ADD COLUMN IF NOT EXISTS commune VARCHAR(255);
