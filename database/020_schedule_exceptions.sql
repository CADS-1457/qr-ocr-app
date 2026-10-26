BEGIN;

-- Libellés paramétrables des exceptions de planning (fermeture, congé, malade, ...).
CREATE TABLE IF NOT EXISTS schedule_exception_types(
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  color text NOT NULL DEFAULT '#64748b',
  blocks_booking boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_schedule_exception_types_name ON schedule_exception_types(lower(name));

-- Exception sur une journée ou une plage de dates, pour toutes les salles ou une salle / un médecin.
CREATE TABLE IF NOT EXISTS schedule_exceptions(
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  type_id uuid NOT NULL REFERENCES schedule_exception_types(id) ON DELETE RESTRICT,
  room_id uuid REFERENCES rooms(id) ON DELETE CASCADE,
  resource_id uuid REFERENCES people_resources(id) ON DELETE CASCADE,
  start_date date NOT NULL,
  end_date date NOT NULL,
  note text,
  created_by integer REFERENCES useraccount(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (end_date >= start_date)
);
CREATE INDEX IF NOT EXISTS ix_schedule_exceptions_dates ON schedule_exceptions(start_date, end_date);

INSERT INTO schedule_exception_types(name, color, blocks_booking)
SELECT v.name, v.color, v.blocks
FROM (VALUES
  ('Fermeture', '#dc2626', true),
  ('Congé', '#f59e0b', true),
  ('Malade', '#7c3aed', true),
  ('Autre', '#64748b', false)
) AS v(name, color, blocks)
WHERE NOT EXISTS (SELECT 1 FROM schedule_exception_types);

COMMIT;
