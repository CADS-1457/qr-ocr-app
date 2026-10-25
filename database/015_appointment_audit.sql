-- Journal d'audit des rendez-vous : conservé même après suppression du rendez-vous.
CREATE TABLE IF NOT EXISTS appointment_audit (
  id bigserial PRIMARY KEY,
  appointment_id uuid NOT NULL,
  action text NOT NULL CHECK (action IN ('CREATE','UPDATE','MOVE','DELETE')),
  changed_by integer,
  changed_at timestamptz NOT NULL DEFAULT now(),
  before_data jsonb,
  after_data jsonb
);
CREATE INDEX IF NOT EXISTS appointment_audit_appointment_idx ON appointment_audit(appointment_id, changed_at);
