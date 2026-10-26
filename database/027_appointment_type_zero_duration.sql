-- Durée 0 = utiliser la durée par défaut de la plage de l'agenda.
ALTER TABLE appointment_types DROP CONSTRAINT IF EXISTS appointment_types_duration_minutes_check;
ALTER TABLE appointment_types ADD CONSTRAINT appointment_types_duration_nonneg CHECK(duration_minutes>=0);
