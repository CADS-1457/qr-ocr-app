BEGIN;
-- Horaires facultatifs d'une exception : sans heure, elle couvre toute la journée.
ALTER TABLE schedule_exceptions ADD COLUMN IF NOT EXISTS start_time time;
ALTER TABLE schedule_exceptions ADD COLUMN IF NOT EXISTS end_time time;
ALTER TABLE schedule_exceptions DROP CONSTRAINT IF EXISTS schedule_exceptions_time_check;
ALTER TABLE schedule_exceptions ADD CONSTRAINT schedule_exceptions_time_check
  CHECK (start_time IS NULL OR end_time IS NULL OR end_time > start_time);
COMMIT;