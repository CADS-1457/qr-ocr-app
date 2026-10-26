-- Pastille de statut : réglage par salle, surchargeable par rendez-vous, couleurs par statut.
ALTER TABLE rooms ADD COLUMN IF NOT EXISTS show_status boolean NOT NULL DEFAULT false;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS show_status boolean;
CREATE TABLE IF NOT EXISTS appointment_status_colors(state text PRIMARY KEY, color text NOT NULL);
-- Statuts configurables (les valeurs personnalisées sont ajoutées à l'enum appointment_state par l'API).
CREATE TABLE IF NOT EXISTS appointment_statuses(code text PRIMARY KEY, label text NOT NULL, color text NOT NULL, edge boolean NOT NULL DEFAULT false, builtin boolean NOT NULL DEFAULT false, active boolean NOT NULL DEFAULT true, sort_order integer NOT NULL DEFAULT 0);
