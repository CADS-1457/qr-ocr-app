BEGIN;
CREATE TABLE IF NOT EXISTS correspondence.recipient (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), document_id uuid NOT NULL REFERENCES correspondence.document(id) ON DELETE CASCADE,
 provider_id uuid REFERENCES provider.healthcare_provider(id), recipient_type varchar(30) NOT NULL DEFAULT 'provider',
 network_identifier text, display_name text, is_primary boolean NOT NULL DEFAULT true);
CREATE TABLE IF NOT EXISTS correspondence.transmission (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), document_id uuid NOT NULL REFERENCES correspondence.document(id),
 recipient_id uuid NOT NULL REFERENCES correspondence.recipient(id), channel varchar(40) NOT NULL,
 integration_provider_id uuid REFERENCES integration.provider(id), status varchar(30) NOT NULL DEFAULT 'draft',
 correlation_id text, external_message_id text, submitted_at timestamptz, delivered_at timestamptz,
 read_at timestamptz, failed_at timestamptz, error_code text, error_message text,
 idempotency_key uuid NOT NULL DEFAULT gen_random_uuid() UNIQUE, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS correspondence.attachment (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), document_id uuid NOT NULL REFERENCES correspondence.document(id) ON DELETE CASCADE,
 filename text NOT NULL, media_type varchar(120), storage_key text NOT NULL, sha256 char(64) NOT NULL,
 size_bytes bigint NOT NULL CHECK(size_bytes>=0), created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS idx_correspondence_transmission_status ON correspondence.transmission(status, created_at);
COMMIT;
