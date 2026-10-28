BEGIN;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS patient;
CREATE SCHEMA IF NOT EXISTS provider;
CREATE SCHEMA IF NOT EXISTS agenda;
CREATE SCHEMA IF NOT EXISTS encounter;
CREATE SCHEMA IF NOT EXISTS inami;
CREATE SCHEMA IF NOT EXISTS insurability;
CREATE SCHEMA IF NOT EXISTS billing;
CREATE SCHEMA IF NOT EXISTS correspondence;
CREATE SCHEMA IF NOT EXISTS inventory;
CREATE SCHEMA IF NOT EXISTS notification;
CREATE SCHEMA IF NOT EXISTS integration;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS migration;

CREATE TABLE IF NOT EXISTS core.organization (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name text NOT NULL,
 enterprise_number varchar(20), created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS core.facility (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL REFERENCES core.organization(id),
 code varchar(50) NOT NULL, name text NOT NULL, timezone varchar(64) NOT NULL DEFAULT 'Europe/Brussels',
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(organization_id, code));
CREATE TABLE IF NOT EXISTS core.app_user (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL REFERENCES core.organization(id),
 external_subject text, email citext, display_name text NOT NULL, active boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS core.module_definition (
 code varchar(80) PRIMARY KEY, name text NOT NULL, dependencies jsonb NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS core.facility_module (
 facility_id uuid REFERENCES core.facility(id) ON DELETE CASCADE,
 module_code varchar(80) REFERENCES core.module_definition(code) ON DELETE CASCADE,
 enabled boolean NOT NULL DEFAULT false, config jsonb NOT NULL DEFAULT '{}',
 PRIMARY KEY(facility_id,module_code));

CREATE TABLE IF NOT EXISTS migration.id_map (
 entity_type varchar(80) NOT NULL, legacy_id text NOT NULL, new_id uuid NOT NULL,
 migrated_at timestamptz NOT NULL DEFAULT now(), checksum text,
 PRIMARY KEY(entity_type, legacy_id), UNIQUE(entity_type,new_id));
CREATE TABLE IF NOT EXISTS migration.run (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), phase varchar(80) NOT NULL,
 started_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
 status varchar(30) NOT NULL DEFAULT 'running', dry_run boolean NOT NULL,
 read_count bigint NOT NULL DEFAULT 0, inserted_count bigint NOT NULL DEFAULT 0,
 skipped_count bigint NOT NULL DEFAULT 0, error_count bigint NOT NULL DEFAULT 0,
 details jsonb NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS migration.reject (
 id bigserial PRIMARY KEY, run_id uuid REFERENCES migration.run(id), entity_type varchar(80),
 legacy_id text, reason text NOT NULL, payload jsonb, created_at timestamptz NOT NULL DEFAULT now());

INSERT INTO core.module_definition(code,name,dependencies) VALUES
('patients','Patients','[]'),('providers','Prestataires','[]'),
('agenda','Agenda','["patients","providers"]'),('encounters','Consultations','["patients","providers"]'),
('inami','Nomenclature INAMI','[]'),('integrations','Intégrations','[]'),
('insurability','Assurabilité','["patients","integrations"]'),
('billing','Facturation','["patients","providers","encounters","inami","integrations"]'),
('correspondence','Courrier','["patients"]'),('inventory','Stock','["encounters"]'),
('notifications','Notifications','["patients","agenda"]'),('audit','Audit','[]')
ON CONFLICT(code) DO NOTHING;
COMMIT;
