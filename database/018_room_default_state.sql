-- Statut proposé par défaut lorsqu'un patient est transféré vers cette salle.
ALTER TABLE rooms ADD COLUMN IF NOT EXISTS default_state text;
ALTER TABLE rooms DROP CONSTRAINT IF EXISTS rooms_default_state_check;
ALTER TABLE rooms ADD CONSTRAINT rooms_default_state_check
  CHECK (default_state IS NULL OR default_state IN
    ('PRESENT','WAITING','IN_PROGRESS','RECOVERY','BILLING','COMPLETED'));
