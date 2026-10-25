BEGIN;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE TABLE IF NOT EXISTS schema_migrations(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());

DO $$ BEGIN CREATE TYPE room_kind AS ENUM ('WAITING','CONSULTATION','OPERATING','RECOVERY','BILLING','OTHER'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE appointment_state AS ENUM ('PLANNED','PRESENT','WAITING','IN_PROGRESS','RECOVERY','BILLING','COMPLETED','CANCELLED','NO_SHOW','POSTPONED'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE follow_up_state AS ENUM ('TO_PLAN','PLANNED','CANCELLED','DONE'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE stock_movement_kind AS ENUM ('RESERVATION','RELEASE_RESERVATION','OUT','RETURN','TRANSFER','ADJUSTMENT'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE notification_channel AS ENUM ('SMS','EMAIL'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE notification_state AS ENUM ('PENDING','PROCESSING','SENT','FAILED','CANCELLED'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS calendars(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name text NOT NULL UNIQUE, color varchar(7) NOT NULL DEFAULT '#2563EB', active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS rooms(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), calendar_id uuid REFERENCES calendars(id) ON DELETE SET NULL, name text NOT NULL UNIQUE,
 kind room_kind NOT NULL DEFAULT 'OTHER', color varchar(7) NOT NULL DEFAULT '#64748B', active boolean NOT NULL DEFAULT true,
 capacity integer NOT NULL DEFAULT 1 CHECK(capacity>0), created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS people_resources(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), kind text NOT NULL CHECK(kind IN ('DOCTOR','OPERATOR')), display_name text NOT NULL,
 active boolean NOT NULL DEFAULT true, external_ref text, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(kind,display_name));
CREATE TABLE IF NOT EXISTS room_opening_slots(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), room_id uuid NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
 weekday smallint NOT NULL CHECK(weekday BETWEEN 0 AND 6), start_time time NOT NULL, end_time time NOT NULL,
 valid_from date, valid_to date, CHECK(start_time<end_time));
CREATE TABLE IF NOT EXISTS resource_opening_slots(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), resource_id uuid NOT NULL REFERENCES people_resources(id) ON DELETE CASCADE,
 weekday smallint NOT NULL CHECK(weekday BETWEEN 0 AND 6), start_time time NOT NULL, end_time time NOT NULL,
 valid_from date, valid_to date, CHECK(start_time<end_time));
CREATE TABLE IF NOT EXISTS calendar_exceptions(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), room_id uuid REFERENCES rooms(id) ON DELETE CASCADE,
 resource_id uuid REFERENCES people_resources(id) ON DELETE CASCADE, starts_at timestamptz NOT NULL, ends_at timestamptz NOT NULL,
 available boolean NOT NULL DEFAULT false, reason text, CHECK(ends_at>starts_at), CHECK((room_id IS NOT NULL) <> (resource_id IS NOT NULL)));
CREATE TABLE IF NOT EXISTS appointment_types(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name text NOT NULL UNIQUE, duration_minutes integer NOT NULL CHECK(duration_minutes>0),
 color varchar(7) NOT NULL DEFAULT '#2563EB', doctor_required boolean NOT NULL DEFAULT false, operator_required boolean NOT NULL DEFAULT false,
 default_sms_minutes integer[] NOT NULL DEFAULT ARRAY[1440], active boolean NOT NULL DEFAULT true);
CREATE TABLE IF NOT EXISTS appointments(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), patient_id uuid NOT NULL, appointment_type_id uuid REFERENCES appointment_types(id),
 room_id uuid NOT NULL REFERENCES rooms(id), doctor_id uuid REFERENCES people_resources(id), operator_id uuid REFERENCES people_resources(id),
 starts_at timestamptz NOT NULL, ends_at timestamptz NOT NULL, state appointment_state NOT NULL DEFAULT 'PLANNED',
 current_location_id uuid REFERENCES rooms(id), phone text, notes text, version integer NOT NULL DEFAULT 1,
 created_by uuid, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), CHECK(ends_at>starts_at));
ALTER TABLE appointments DROP CONSTRAINT IF EXISTS appointments_room_no_overlap;
ALTER TABLE appointments ADD CONSTRAINT appointments_room_no_overlap EXCLUDE USING gist (room_id WITH =, tstzrange(starts_at,ends_at,'[)') WITH &&) WHERE (state NOT IN ('CANCELLED','NO_SHOW','POSTPONED'));
CREATE INDEX IF NOT EXISTS idx_appointments_day ON appointments(starts_at);
CREATE INDEX IF NOT EXISTS idx_appointments_patient ON appointments(patient_id,starts_at DESC);

CREATE TABLE IF NOT EXISTS appointment_status_history(
 id bigserial PRIMARY KEY, appointment_id uuid NOT NULL REFERENCES appointments(id) ON DELETE CASCADE,
 old_state appointment_state, new_state appointment_state NOT NULL, changed_by uuid, changed_at timestamptz NOT NULL DEFAULT now(), comment text);
CREATE TABLE IF NOT EXISTS patient_flow_history(
 id bigserial PRIMARY KEY, appointment_id uuid NOT NULL REFERENCES appointments(id) ON DELETE CASCADE,
 from_location_id uuid REFERENCES rooms(id), to_location_id uuid REFERENCES rooms(id),
 transferred_by uuid, transferred_at timestamptz NOT NULL DEFAULT now(), handover_comment text);
CREATE TABLE IF NOT EXISTS recovery_notes(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), appointment_id uuid NOT NULL REFERENCES appointments(id) ON DELETE CASCADE,
 room_id uuid REFERENCES rooms(id), comment text NOT NULL, created_by uuid, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS discharge_instructions(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), appointment_id uuid NOT NULL UNIQUE REFERENCES appointments(id) ON DELETE CASCADE,
 instructions text, notes text, created_by uuid, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS follow_up_tasks(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), patient_id uuid NOT NULL, source_appointment_id uuid NOT NULL REFERENCES appointments(id),
 appointment_type_id uuid REFERENCES appointment_types(id), doctor_id uuid REFERENCES people_resources(id), target_date date,
 notes text, state follow_up_state NOT NULL DEFAULT 'TO_PLAN', new_appointment_id uuid REFERENCES appointments(id),
 created_by uuid, created_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS stock_locations(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name text NOT NULL UNIQUE, room_id uuid REFERENCES rooms(id), active boolean NOT NULL DEFAULT true);
CREATE TABLE IF NOT EXISTS stock_items(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), sku text UNIQUE, name text NOT NULL, track_lot boolean NOT NULL DEFAULT false,
 track_serial boolean NOT NULL DEFAULT false, consumable boolean NOT NULL DEFAULT true, active boolean NOT NULL DEFAULT true);
CREATE TABLE IF NOT EXISTS stock_units(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), stock_location_id uuid NOT NULL REFERENCES stock_locations(id), stock_item_id uuid NOT NULL REFERENCES stock_items(id),
 lot_number text, serial_number text, expires_on date, quantity numeric(12,3) NOT NULL DEFAULT 0 CHECK(quantity>=0),
 reserved_quantity numeric(12,3) NOT NULL DEFAULT 0 CHECK(reserved_quantity>=0 AND reserved_quantity<=quantity),
 UNIQUE(stock_location_id,stock_item_id,lot_number,serial_number));
CREATE TABLE IF NOT EXISTS appointment_stock_usage(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), appointment_id uuid NOT NULL REFERENCES appointments(id) ON DELETE CASCADE,
 stock_unit_id uuid NOT NULL REFERENCES stock_units(id), planned_quantity numeric(12,3) NOT NULL DEFAULT 0,
 reserved_quantity numeric(12,3) NOT NULL DEFAULT 0, used_quantity numeric(12,3) NOT NULL DEFAULT 0,
 returned_quantity numeric(12,3) NOT NULL DEFAULT 0, CHECK(planned_quantity>=0 AND reserved_quantity>=0 AND used_quantity>=0 AND returned_quantity>=0),
 UNIQUE(appointment_id,stock_unit_id));
CREATE TABLE IF NOT EXISTS stock_movements(
 id bigserial PRIMARY KEY, stock_unit_id uuid NOT NULL REFERENCES stock_units(id), appointment_id uuid REFERENCES appointments(id), patient_id uuid,
 kind stock_movement_kind NOT NULL, quantity numeric(12,3) NOT NULL CHECK(quantity>0), created_by uuid,
 created_at timestamptz NOT NULL DEFAULT now(), comment text);
CREATE INDEX IF NOT EXISTS idx_stock_movements_appointment ON stock_movements(appointment_id,created_at);

CREATE TABLE IF NOT EXISTS patient_communication_preferences(
 patient_id uuid PRIMARY KEY, sms_enabled boolean NOT NULL DEFAULT true, email_enabled boolean NOT NULL DEFAULT true,
 preferred_channel notification_channel, preferred_language varchar(10), updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS notification_templates(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), template_key text NOT NULL, channel notification_channel NOT NULL,
 subject text, body text NOT NULL, active boolean NOT NULL DEFAULT true, UNIQUE(template_key,channel));
CREATE TABLE IF NOT EXISTS notifications(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), patient_id uuid NOT NULL, appointment_id uuid REFERENCES appointments(id) ON DELETE CASCADE,
 channel notification_channel NOT NULL, template_key text NOT NULL, scheduled_at timestamptz NOT NULL, sent_at timestamptz,
 recipient text, recipient_name text, state notification_state NOT NULL DEFAULT 'PENDING', provider text, provider_message_id text,
 attempts integer NOT NULL DEFAULT 0, last_error text, created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS idx_notifications_pending ON notifications(channel,state,scheduled_at) WHERE state='PENDING';


INSERT INTO schema_migrations(version) VALUES('010_agenda_patient_flow_stock') ON CONFLICT DO NOTHING;
COMMIT;
