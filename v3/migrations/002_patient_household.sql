BEGIN;
CREATE TABLE IF NOT EXISTS patient.patient (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL REFERENCES core.organization(id),
 national_number_encrypted bytea, national_number_hmac bytea,
 first_name text NOT NULL, last_name text NOT NULL, birth_date date,
 administrative_sex varchar(30), preferred_language varchar(10) DEFAULT 'fr-BE',
 active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(organization_id,national_number_hmac));
CREATE INDEX IF NOT EXISTS idx_patient_name_birth ON patient.patient(organization_id,last_name,first_name,birth_date);

-- Un contact est une valeur réutilisable. Un GSM n'est jamais unique par patient.
CREATE TABLE IF NOT EXISTS patient.contact_point (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL REFERENCES core.organization(id),
 kind varchar(30) NOT NULL CHECK(kind IN ('mobile','phone','email')),
 normalized_value_encrypted bytea NOT NULL, normalized_value_hmac bytea NOT NULL,
 display_label text, verified_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(organization_id,kind,normalized_value_hmac));
CREATE TABLE IF NOT EXISTS patient.patient_contact (
 patient_id uuid REFERENCES patient.patient(id) ON DELETE CASCADE,
 contact_point_id uuid REFERENCES patient.contact_point(id) ON DELETE CASCADE,
 usage varchar(30) NOT NULL DEFAULT 'personal', owner_label text,
 is_primary boolean NOT NULL DEFAULT false, allow_appointment_contact boolean NOT NULL DEFAULT true,
 allow_sms boolean NOT NULL DEFAULT false, valid_from date, valid_to date,
 PRIMARY KEY(patient_id,contact_point_id,usage));

CREATE TABLE IF NOT EXISTS patient.household (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL REFERENCES core.organization(id),
 name text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS patient.household_member (
 household_id uuid REFERENCES patient.household(id) ON DELETE CASCADE,
 patient_id uuid REFERENCES patient.patient(id) ON DELETE CASCADE,
 relationship_to_household varchar(40), is_primary_contact boolean NOT NULL DEFAULT false,
 valid_from date, valid_to date, PRIMARY KEY(household_id,patient_id));
CREATE TABLE IF NOT EXISTS patient.household_contact (
 household_id uuid REFERENCES patient.household(id) ON DELETE CASCADE,
 contact_point_id uuid REFERENCES patient.contact_point(id) ON DELETE CASCADE,
 label text, allow_appointment_contact boolean NOT NULL DEFAULT true,
 allow_sms boolean NOT NULL DEFAULT false, PRIMARY KEY(household_id,contact_point_id));
CREATE TABLE IF NOT EXISTS patient.relationship (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), patient_id uuid NOT NULL REFERENCES patient.patient(id) ON DELETE CASCADE,
 related_patient_id uuid NOT NULL REFERENCES patient.patient(id) ON DELETE CASCADE,
 relationship_type varchar(40) NOT NULL, is_legal_representative boolean NOT NULL DEFAULT false,
 is_emergency_contact boolean NOT NULL DEFAULT false, can_manage_appointments boolean NOT NULL DEFAULT false,
 valid_from date, valid_to date, created_at timestamptz NOT NULL DEFAULT now(),
 CHECK(patient_id<>related_patient_id), UNIQUE(patient_id,related_patient_id,relationship_type,valid_from));
CREATE TABLE IF NOT EXISTS patient.consent (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), patient_id uuid NOT NULL REFERENCES patient.patient(id) ON DELETE CASCADE,
 purpose varchar(120) NOT NULL, status varchar(30) NOT NULL, granted_at timestamptz, withdrawn_at timestamptz,
 evidence_ref text, created_at timestamptz NOT NULL DEFAULT now());
COMMIT;
