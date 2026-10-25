BEGIN;

-- Nombre maximal de rendez-vous simultanés par salle/agenda (1 = pas de surréservation).
ALTER TABLE rooms ADD COLUMN IF NOT EXISTS max_parallel integer NOT NULL DEFAULT 1
  CHECK (max_parallel BETWEEN 1 AND 20);

-- La contrainte d'exclusion interdisait tout chevauchement dans une salle.
-- Le plafond est désormais contrôlé par l'API (verrou par salle) selon rooms.max_parallel.
ALTER TABLE appointments DROP CONSTRAINT IF EXISTS appointments_room_no_overlap;

INSERT INTO schema_migrations(version) VALUES('016_room_overbooking') ON CONFLICT DO NOTHING;
COMMIT;
