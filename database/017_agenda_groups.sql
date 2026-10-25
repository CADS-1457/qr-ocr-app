BEGIN;

-- Groupes d'agendas : ensembles de salles affichés ensemble dans le calendrier.
CREATE TABLE IF NOT EXISTS agenda_groups(
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_agenda_groups_name ON agenda_groups(lower(name));

CREATE TABLE IF NOT EXISTS agenda_group_rooms(
  group_id uuid NOT NULL REFERENCES agenda_groups(id) ON DELETE CASCADE,
  room_id uuid NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
  PRIMARY KEY (group_id, room_id)
);

INSERT INTO schema_migrations(version) VALUES('017_agenda_groups') ON CONFLICT DO NOTHING;
COMMIT;
