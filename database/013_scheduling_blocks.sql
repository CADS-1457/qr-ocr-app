BEGIN;

-- Plages (blocs) pendant lesquelles on peut programmer des rendez-vous.
-- Une salle sans plage active reste librement réservable.
-- weekday : 0 = lundi ... 6 = dimanche. slot_minutes : pas de programmation en minutes.
CREATE TABLE IF NOT EXISTS scheduling_blocks(
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  room_id uuid NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
  resource_id uuid REFERENCES people_resources(id) ON DELETE CASCADE,
  weekday smallint NOT NULL CHECK (weekday BETWEEN 0 AND 6),
  start_time time NOT NULL,
  end_time time NOT NULL,
  slot_minutes integer NOT NULL CHECK (slot_minutes BETWEEN 1 AND 1440),
  label text,
  valid_from date,
  valid_to date,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (start_time < end_time),
  CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_from <= valid_to)
);
CREATE INDEX IF NOT EXISTS idx_scheduling_blocks_room_day ON scheduling_blocks(room_id, weekday) WHERE active;
CREATE INDEX IF NOT EXISTS idx_scheduling_blocks_resource ON scheduling_blocks(resource_id) WHERE resource_id IS NOT NULL;

INSERT INTO schema_migrations(version) VALUES('013_scheduling_blocks') ON CONFLICT DO NOTHING;
COMMIT;
